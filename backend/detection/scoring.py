"""Score a board-plane point (mm) or an image pixel (via a camera's
calibration homography) - board mm -> polar -> segment/ring/value.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from calibration import board_model


def score_board_point(x_mm: float, y_mm: float) -> dict:
    radius = math.hypot(x_mm, y_mm)
    r = board_model.RADII_MM

    if radius <= r["bull_in"]:
        return {"segment": 25, "ring": "bullseye", "multiplier": 2, "value": 50}
    if radius <= r["bull_out"]:
        return {"segment": 25, "ring": "outer_bull", "multiplier": 1, "value": 25}
    if radius > r["double_out"]:
        # Still landed on the physical board face (which extends well beyond
        # the scoring radius) - a real thrown dart worth zero, not a
        # detection failure. See board_model.PHYSICAL_BOARD_RADIUS_MM.
        return {"segment": None, "ring": "out", "multiplier": 0, "value": 0}

    angle = math.atan2(x_mm, -y_mm) % (2 * math.pi)  # clockwise from top, matches board_model
    wedge = board_model.WEDGE
    idx = int(((angle + wedge / 2) // wedge) % 20)
    segment = board_model.SEGMENTS[idx]

    if radius <= r["triple_in"]:
        ring, multiplier = "single", 1
    elif radius <= r["triple_out"]:
        ring, multiplier = "triple", 3
    elif radius <= r["double_in"]:
        ring, multiplier = "single", 1
    else:
        ring, multiplier = "double", 2

    return {"segment": segment, "ring": ring, "multiplier": multiplier, "value": segment * multiplier}


def score_point(tip_px: tuple[float, float], homography: list) -> dict:
    """Convenience wrapper: image pixel -> board mm (via a camera's own
    homography) -> score. Kept for single-camera debug/preview use; the
    multi-camera pipeline scores the *fused* board-mm point directly via
    score_board_point, once per dart, not once per camera."""
    H = np.array(homography, dtype=np.float32)
    pt = np.array([[tip_px]], dtype=np.float32)  # shape (1, 1, 2)
    board_pt = cv2.perspectiveTransform(pt, H)[0, 0]
    bx, by = float(board_pt[0]), float(board_pt[1])
    return {**score_board_point(bx, by), "board_point": [bx, by]}
