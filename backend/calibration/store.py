"""Per-camera calibration persistence - config/calibration.json.

Each camera's profile is 5 reference points (image pixel space) - center
plus the outer-double point of 4 landmark segments - and the homography
derived from them, mapping image pixels to board-space millimetres.
"""
from __future__ import annotations

import json
import threading

import cv2
import numpy as np

from paths import CONFIG_DIR

from . import board_model

CALIBRATION_PATH = CONFIG_DIR / "calibration.json"

# Segments the manual calibration flow asks the user to click, in order -
# a true 90-degree cross around the board (20 top, 6 right, 3 bottom, 11
# left in the standard clockwise segment order), so it's easy to reason
# about regardless of a camera's actual mounting angle.
LANDMARKS: tuple[int, ...] = (20, 6, 3, 11)
POINT_KEYS = ["center", *[str(s) for s in LANDMARKS]]

_lock = threading.Lock()


def _load_raw() -> dict:
    if CALIBRATION_PATH.exists():
        try:
            return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_raw(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CALIBRATION_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(CALIBRATION_PATH)


def get(camera_id: int) -> dict | None:
    with _lock:
        return _load_raw().get(str(camera_id))


def get_all() -> dict:
    with _lock:
        return _load_raw()


def clear(camera_id: int) -> None:
    with _lock:
        data = _load_raw()
        data.pop(str(camera_id), None)
        _save_raw(data)


def _compute_homography(points_px: dict[str, list[float]]) -> list:
    board_pts_mm = board_model.landmark_points_mm(LANDMARKS)
    src = np.array([points_px[k] for k in POINT_KEYS], dtype=np.float32).reshape(-1, 1, 2)
    dst = np.array([board_pts_mm[k] for k in POINT_KEYS], dtype=np.float32).reshape(-1, 1, 2)
    H, _ = cv2.findHomography(src, dst, method=0)
    if H is None:
        raise ValueError("could not compute a calibration from these points - are they collinear?")
    return H.tolist()


def save_points(camera_id: int, points_px: dict[str, list[float]], source: str) -> dict:
    missing = set(POINT_KEYS) - set(points_px)
    if missing:
        raise ValueError(f"missing points: {sorted(missing)}")
    homography = _compute_homography(points_px)
    with _lock:
        data = _load_raw()
        data[str(camera_id)] = {
            "points": {k: list(points_px[k]) for k in POINT_KEYS},
            "homography": homography,
            "source": source,
        }
        _save_raw(data)
        return data[str(camera_id)]


def save_homography(camera_id: int, homography: list, source: str) -> dict:
    """Persist a homography that was solved from something other than the 5
    clicked points (the colour fine-tune).

    The 5 stored points are re-derived from it by projecting the ideal
    landmark positions back into image space, so `points` and `homography`
    never disagree - the manual editor keeps working, and reopening it shows
    handles that sit on the refined fit rather than the old clicks.
    """
    H = np.array(homography, dtype=np.float64)
    H_inv = np.linalg.inv(H).astype(np.float32)
    board_pts_mm = board_model.landmark_points_mm(LANDMARKS)
    src = np.array([board_pts_mm[k] for k in POINT_KEYS], dtype=np.float32).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(src, H_inv).reshape(-1, 2)
    points = {k: [float(x), float(y)] for k, (x, y) in zip(POINT_KEYS, projected)}

    with _lock:
        data = _load_raw()
        data[str(camera_id)] = {
            "points": points,
            "homography": [list(map(float, row)) for row in H],
            "source": source,
        }
        _save_raw(data)
        return data[str(camera_id)]


def grid_for(camera_id: int) -> dict | None:
    """Project the standard ring/sector grid through this camera's
    homography into image pixel space, ready for the frontend to draw."""
    profile = get(camera_id)
    if profile is None:
        return None

    H = np.array(profile["homography"], dtype=np.float64)
    H_inv = np.linalg.inv(H).astype(np.float32)
    geometry = board_model.grid_geometry_mm()

    def project(mm_pts):
        arr = np.array(mm_pts, dtype=np.float32).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(arr, H_inv).reshape(-1, 2).tolist()

    rings_px = [project(ring) for ring in geometry["rings"]]
    sector_lines_px = [project(list(line)) for line in geometry["sector_lines"]]
    labels_px = []
    for label in geometry["labels"]:
        [pt] = project([label["pos"]])
        labels_px.append({"segment": label["segment"], "x": pt[0], "y": pt[1]})

    return {
        "points": profile["points"],
        "source": profile["source"],
        "rings": rings_px,
        "sector_lines": sector_lines_px,
        "labels": labels_px,
    }
