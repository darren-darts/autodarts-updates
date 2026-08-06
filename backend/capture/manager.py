"""Camera capture: one background thread per open device, ref-counted.

A CameraStream is opened on first acquire (e.g. a browser starting a
preview) and closed when the last client releases it, so cameras are
only held while something is actually watching.
"""
from __future__ import annotations

import logging
import platform
import threading
import time

import cv2
import numpy as np

log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"
# DirectShow is preferred (faster to open) but unreliable for some UVC
# cameras - notably several identically-named devices on one hub, where its
# index-based enumeration doesn't reliably match the same physical device
# across opens. Media Foundation opens those same devices correctly, so it's
# a fallback rather than the default (DSHOW is a bit lower-latency when it
# works). Linux/Pi only ever uses V4L2.
OPEN_BACKENDS = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if IS_WINDOWS else [cv2.CAP_V4L2]
JPEG_QUALITY = 80
REOPEN_DELAY_S = 2.0


class CameraStream:
    def __init__(self, device_id: int, width: int, height: int, fps: int):
        self.device_id = device_id
        self.width = width
        self.height = height
        self.fps = fps
        self.error: str | None = None
        self._jpeg: bytes | None = None
        self._frame_no = 0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"camera-{device_id}", daemon=True
        )
        self._thread.start()

    def _open(self) -> cv2.VideoCapture | None:
        for backend in OPEN_BACKENDS:
            cap = cv2.VideoCapture(self.device_id, backend)
            if not cap.isOpened():
                cap.release()
                continue
            # MJPG keeps USB bandwidth manageable with multiple cameras
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            # Confirm it actually delivers frames - MSMF in particular can
            # report isOpened() True but then fail to start streaming (e.g.
            # if another handle already has the device).
            ok, _ = cap.read()
            if ok:
                return cap
            cap.release()
        return None

    def _run(self) -> None:
        cap = None
        try:
            while not self._stop.is_set():
                if cap is None:
                    cap = self._open()
                    if cap is None:
                        self.error = f"cannot open device {self.device_id}"
                        if self._stop.wait(REOPEN_DELAY_S):
                            break
                        continue
                    self.error = None
                ok, frame = cap.read()
                if not ok:
                    self.error = f"read failed on device {self.device_id}"
                    cap.release()
                    cap = None
                    continue
                ok, buf = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                )
                if not ok:
                    continue
                with self._cond:
                    self._jpeg = buf.tobytes()
                    self._frame_no += 1
                    self._cond.notify_all()
        finally:
            if cap is not None:
                cap.release()
            with self._cond:
                self._cond.notify_all()

    def next_jpeg(self, last_frame_no: int, timeout: float = 1.0):
        """Block until a frame newer than last_frame_no exists.

        Returns (jpeg_bytes | None, frame_no).
        """
        with self._cond:
            self._cond.wait_for(
                lambda: self._frame_no > last_frame_no or self._stop.is_set(),
                timeout=timeout,
            )
            return self._jpeg, self._frame_no

    def peek(self):
        """Return whatever the current frame is right now, without waiting -
        for a coordinator polling several cameras in one loop (e.g. the
        multi-camera detection session), which does its own pacing rather
        than blocking on any single camera's next frame."""
        with self._cond:
            return self._jpeg, self._frame_no

    def health(self) -> dict:
        """Whether this camera is actually delivering frames, and why not if
        it isn't. The worker retries a dead device forever in the background,
        so without surfacing this a camera can sit silently broken while the
        UI looks entirely normal."""
        with self._cond:
            return {
                "device_id": self.device_id,
                "frames": self._frame_no,
                "delivering": self._jpeg is not None,
                "error": self.error,
            }

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class CaptureManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._streams: dict[int, CameraStream] = {}
        self._refs: dict[int, int] = {}

    def acquire(self, device_id: int, width: int, height: int, fps: int) -> CameraStream:
        with self._lock:
            stream = self._streams.get(device_id)
            if stream is None:
                stream = CameraStream(device_id, width, height, fps)
                self._streams[device_id] = stream
                self._refs[device_id] = 0
            self._refs[device_id] += 1
            return stream

    def release(self, device_id: int) -> None:
        with self._lock:
            if device_id not in self._streams:
                return
            self._refs[device_id] -= 1
            if self._refs[device_id] <= 0:
                stream = self._streams.pop(device_id)
                self._refs.pop(device_id)
                stream.stop()
                log.info("camera %s released and closed", device_id)

    def grab_frame_bgr(self, device_id: int, width: int, height: int, fps: int, timeout: float = 5.0):
        """Acquire, wait for one fresh frame decoded to BGR, release.
        For one-off snapshots (e.g. calibration) rather than live viewing."""
        stream = self.acquire(device_id, width, height, fps)
        try:
            jpeg, _ = stream.next_jpeg(0, timeout=timeout)
            if jpeg is None:
                raise RuntimeError(stream.error or f"no frame available from device {device_id}")
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if bgr is None:
                raise RuntimeError(f"failed to decode a frame from device {device_id}")
            return bgr
        finally:
            self.release(device_id)

    def stop_all(self) -> None:
        with self._lock:
            for stream in self._streams.values():
                stream.stop()
            self._streams.clear()
            self._refs.clear()
