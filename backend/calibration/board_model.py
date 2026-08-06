"""Standard dartboard geometry (PDC/WDF spec), independent of any camera.

Board space: origin at the bullseye, millimetres, x+ = right, y+ = down
(same sense as image pixel space, which keeps the homography intuitive).
Angle 0 = top (12 o'clock), increasing clockwise.
"""
from __future__ import annotations

import math

RADII_MM = {
    "bull_in": 6.35,
    "bull_out": 15.9,
    "triple_in": 99.0,
    "triple_out": 107.0,
    "double_in": 162.0,
    "double_out": 170.0,
}
SCORING_RADIUS_MM = RADII_MM["double_out"]
# A regulation bristle board is ~451mm across. Detection can cover the full
# board face even though scoring stops at the outer double wire - a dart
# that's on the board but outside 170mm is a real "OUT" (zero, still a
# thrown dart), not a detection failure.
PHYSICAL_BOARD_RADIUS_MM = 225.5
# How far past the scoring radius the physical board face extends - the
# region detection's ROI mask needs to cover (a dart can land there and be a
# real, scoreless "OUT" throw), even though scoring itself stops at 170mm.
DETECTION_MASK_MARGIN_MM = PHYSICAL_BOARD_RADIUS_MM - SCORING_RADIUS_MM

# Segment numbers clockwise from top.
SEGMENTS = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]

WEDGE = 2 * math.pi / 20


def segment_center_angle_rad(segment: int) -> float:
    """Angle (clockwise from top) of the given segment's center, assuming
    an upright board (segment 20 exactly at top)."""
    return SEGMENTS.index(segment) * WEDGE


def board_point_mm(angle_rad: float, radius_mm: float) -> tuple[float, float]:
    return radius_mm * math.sin(angle_rad), -radius_mm * math.cos(angle_rad)


def landmark_points_mm(landmarks: tuple[int, ...]) -> dict[str, tuple[float, float]]:
    """Board-space (mm) coordinates for the center plus each landmark
    segment's outer-double point - the standard reference-point set used
    for calibration."""
    points = {"center": (0.0, 0.0)}
    for seg in landmarks:
        angle = segment_center_angle_rad(seg)
        points[str(seg)] = board_point_mm(angle, RADII_MM["double_out"])
    return points


def grid_geometry_mm(n_ring_points: int = 96) -> dict:
    """Ring polygons, the 20 sector boundary lines, and segment number label
    positions, all in board-space mm - ready to be projected through a
    homography into any camera's image space."""
    rings = []
    for r in RADII_MM.values():
        pts = [
            board_point_mm(t, r)
            for t in (i * 2 * math.pi / n_ring_points for i in range(n_ring_points))
        ]
        rings.append(pts)

    sector_lines = []
    r_in = RADII_MM["bull_out"]
    r_out = RADII_MM["double_out"]
    for i in range(20):
        # boundary between segment i and segment i+1 sits halfway between
        # their centers
        angle = i * WEDGE + WEDGE / 2
        sector_lines.append((board_point_mm(angle, r_in), board_point_mm(angle, r_out)))

    r_label = (RADII_MM["triple_out"] + RADII_MM["double_in"]) / 2
    labels = [
        {"segment": seg, "pos": board_point_mm(i * WEDGE, r_label)}
        for i, seg in enumerate(SEGMENTS)
    ]

    return {"rings": rings, "sector_lines": sector_lines, "labels": labels}


def distance_to_scoring_wire(x_mm: float, y_mm: float) -> float:
    """Shortest board-plane distance from a point to any score-changing
    wire - the circular bull/triple/double boundaries always count; radial
    segment wires only matter between the outer bull and outer double
    (there's no scoring wire in the single-bed area's middle). Used to
    gauge how much positional uncertainty a fused hit can tolerate before
    the *score* itself becomes ambiguous, not just the position."""
    radius = math.hypot(x_mm, y_mm)
    distances = [abs(radius - r) for r in RADII_MM.values()]

    r_in = RADII_MM["bull_out"]
    r_out = RADII_MM["double_out"]
    for i in range(20):
        angle = i * WEDGE + WEDGE / 2  # a wedge boundary, matches grid_geometry_mm's sector_lines
        sx, sy = board_point_mm(angle, r_in)
        ex, ey = board_point_mm(angle, r_out)
        seg_len_sq = (ex - sx) ** 2 + (ey - sy) ** 2
        t = ((x_mm - sx) * (ex - sx) + (y_mm - sy) * (ey - sy)) / seg_len_sq if seg_len_sq else 0.0
        t = max(0.0, min(1.0, t))
        cx, cy = sx + t * (ex - sx), sy + t * (ey - sy)
        distances.append(math.hypot(x_mm - cx, y_mm - cy))

    return min(distances)
