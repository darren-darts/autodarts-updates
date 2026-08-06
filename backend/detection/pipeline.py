"""Thin lifecycle wrapper around DetectionSession - the multi-camera
detection loop lives in session.py; this just decides which cameras are
eligible (configured + calibrated) and starts/stops one shared session.
"""
from __future__ import annotations

import asyncio
import threading

import settings_store
from calibration import store as calibration_store
from capture.manager import CaptureManager

from .session import DetectionSession


class DetectionManager:
    def __init__(self, capture_manager: CaptureManager):
        self._capture_manager = capture_manager
        self._session: DetectionSession | None = None
        # Kept after stop() so throw history/evidence from the just-ended
        # session can still be reviewed and corrected - a "stop to review"
        # workflow would otherwise lose everything the instant it's stopped.
        # Dropped on the next start(), since a fresh baseline makes its
        # evidence irrelevant to a new session anyway.
        self._last_session: DetectionSession | None = None
        self._lock = threading.Lock()

    def _eligible_camera_ids(self) -> list[int]:
        slots = settings_store.load()["cameras"]["slots"]
        device_ids = [slot["device_id"] for slot in slots if slot is not None]
        return [cid for cid in device_ids if calibration_store.get(cid) is not None]

    def start(self, loop: asyncio.AbstractEventLoop) -> dict:
        with self._lock:
            if self._session is not None:
                return {"started": True, "camera_ids": self._session.camera_ids}
            camera_ids = self._eligible_camera_ids()
            if len(camera_ids) < 2:
                return {"started": False, "error": "need at least 2 calibrated cameras", "camera_ids": camera_ids}
            session = DetectionSession(camera_ids, self._capture_manager, loop)
            self._session = session
            self._last_session = None
            session.start()
            return {"started": True, "camera_ids": camera_ids}

    def stop(self) -> None:
        with self._lock:
            session = self._session
            self._session = None
            if session is not None:
                self._last_session = session
        if session is not None:
            session.stop()

    def stop_all(self) -> None:
        self.stop()

    def _active_or_last(self) -> DetectionSession | None:
        with self._lock:
            return self._session or self._last_session

    def status(self) -> dict:
        with self._lock:
            session = self._session
            last = self._last_session
        if session is None:
            history = last.status()["history"] if last is not None else []
            return {"active": False, "state": "stopped", "message": "Detector is stopped", "history": history}
        return session.status()

    def get_evidence_frame(self, event_id: int, camera_id: int) -> bytes | None:
        session = self._active_or_last()
        return session.get_evidence_frame(event_id, camera_id) if session else None

    def apply_correction(self, event_id: int, camera_id: int, x_px: float, y_px: float) -> dict | None:
        session = self._active_or_last()
        return session.apply_correction(event_id, camera_id, x_px, y_px) if session else None

    def apply_board_correction(self, event_id: int, x_mm: float, y_mm: float) -> dict | None:
        session = self._active_or_last()
        return session.apply_board_correction(event_id, x_mm, y_mm) if session else None

    def start_trace(self) -> dict:
        with self._lock:
            session = self._session
        if session is None:
            return {"tracing": False, "error": "detector is not running"}
        return session.start_trace()

    def stop_trace(self) -> dict:
        session = self._active_or_last()
        return session.stop_trace() if session else {"tracing": False, "samples": 0}

    def trace_dump(self) -> dict:
        session = self._active_or_last()
        return session.trace_dump() if session else {"tracing": False, "samples": []}

    def analyze(self) -> dict:
        session = self._active_or_last()
        return session.analyze() if session else {
            "count": 0, "segment_mismatches": 0, "mismatches_flagged_uncertain": 0,
            "mismatches_not_flagged": 0, "mean_error_mm": None, "max_error_mm": None, "events": [],
        }
