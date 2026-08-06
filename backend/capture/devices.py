"""Enumerate USB camera devices with friendly names.

Windows: DirectShow device names via pygrabber (index order matches
cv2.VideoCapture with CAP_DSHOW). Linux/Pi: /sys/class/video4linux.
"""
from __future__ import annotations

import glob
import logging
import os
import platform
import re

import cv2

log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"


def _list_windows() -> list[dict]:
    try:
        from pygrabber.dshow_graph import FilterGraph

        names = FilterGraph().get_input_devices()
        return [{"id": i, "name": name} for i, name in enumerate(names)]
    except Exception:
        log.exception("pygrabber enumeration failed, falling back to probing")
        devices = []
        for idx in range(6):
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap.isOpened():
                devices.append({"id": idx, "name": f"Camera {idx}"})
            cap.release()
        return devices


def _list_linux() -> list[dict]:
    devices = []
    for path in sorted(glob.glob("/sys/class/video4linux/video*")):
        match = re.search(r"video(\d+)$", path)
        if not match:
            continue
        idx = int(match.group(1))
        try:
            with open(os.path.join(path, "name")) as f:
                name = f.read().strip()
        except OSError:
            name = f"/dev/video{idx}"
        devices.append({"id": idx, "name": f"{name} (/dev/video{idx})"})
    return devices


def list_devices() -> list[dict]:
    """Return [{"id": int, "name": str}, ...] for attached video devices."""
    return _list_windows() if IS_WINDOWS else _list_linux()
