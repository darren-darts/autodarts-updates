"""Best-effort auto-detection of board center/scale/rotation from a single
frame - used to seed the manual calibration flow with a starting guess, not
as a final calibration on its own.

The board cameras are mounted around the board at an angle (not looking
straight at the face), so the double ring appears as an ELLIPSE in-frame,
not a circle - fitting a plain circle (an earlier version of this module
did) systematically misplaces every landmark, worst for the point furthest
from the camera. Center and scale/shape come from color (the bull + the
outer ring's ellipse fit), which is reliable. Rotation only recovers the
wedge-boundary PHASE (mod 18deg) from the black/cream alternation - it
cannot tell which physical segment number is where (that would need
reading the printed numbers), so suggested landmarks may land on the wrong
wedge if the camera is rotated far from upright. That's expected; the user
corrects it via drag in the manual flow. Any suggested point that would
land outside the visible frame is dropped (returned as None) rather than
placed off-screen where it can't be seen or adjusted.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import board_model


def _ring_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    red_mask = (
        cv2.inRange(hsv, (0, 90, 60), (10, 255, 255))
        | cv2.inRange(hsv, (170, 90, 60), (179, 255, 255))
    )
    green_mask = cv2.inRange(hsv, (40, 60, 40), (85, 255, 255))
    mask = cv2.bitwise_or(red_mask, green_mask)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)


def find_center(bgr: np.ndarray, ring_mask: np.ndarray) -> tuple[float, float]:
    h, w = bgr.shape[:2]
    contours, _ = cv2.findContours(ring_mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 30:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        circularity = area / (math.pi * r * r + 1e-6)
        if circularity < 0.5:
            continue
        if abs(cx - w / 2) < w * 0.35 and abs(cy - h / 2) < h * 0.35 and r < w * 0.08:
            candidates.append((area, cx, cy))
    if not candidates:
        raise RuntimeError(
            "could not find the bullseye - is the board fully visible and well lit?"
        )
    candidates.sort(key=lambda t: t[0])
    _, cx, cy = candidates[-1]
    return float(cx), float(cy)


def find_outer_ellipse(
    bgr: np.ndarray, ring_mask: np.ndarray, cx: float, cy: float
) -> dict:
    """Fit an ellipse to the outer (double) ring - a much better model than
    a circle for a camera looking at the board from an angle. Falls back to
    a plain circle (a=b, angle=0) if there aren't enough ring pixels found
    at a consistent distance to fit one reliably."""
    h, w = bgr.shape[:2]
    ys, xs = np.nonzero(ring_mask)
    d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    window = d < min(w, h) * 0.55
    if not window.any():
        raise RuntimeError("no ring-colored pixels found near the detected center")
    rough_r = float(np.percentile(d[window], 99.0))

    # Points plausibly on the outer ring specifically - a NARROW band just
    # inside the 99th-percentile radius. The true double ring is thin
    # (~8mm of a 170mm radius, ~5%); a wide band here catches red/green
    # decorative text/logos on the board's black surround (further out),
    # which badly skews an unweighted least-squares ellipse fit.
    band = window & (d > rough_r * 0.90) & (d < rough_r * 1.02)
    pts = np.column_stack([xs[band], ys[band]]).astype(np.float32)

    # Also require the points to span a reasonably wide angular range - a
    # genuine (partially cropped is fine) ring covers a meaningful arc; a
    # cluster of logo-text pixels that happened to land in the radial band
    # would only cover a small one.
    angular_spread = 0.0
    if len(pts) >= 30:
        point_angles = np.arctan2(pts[:, 0] - cx, -(pts[:, 1] - cy))
        hist, _ = np.histogram(point_angles, bins=24, range=(-math.pi, math.pi))
        angular_spread = float(np.count_nonzero(hist)) / 24

    if len(pts) < 30 or angular_spread < 0.25:
        return {"cx": cx, "cy": cy, "a": rough_r, "b": rough_r, "angle_deg": 0.0}

    (ecx, ecy), (minor_ax, major_ax), angle_deg = cv2.fitEllipse(pts.reshape(-1, 1, 2))
    a, b = major_ax / 2, minor_ax / 2
    # sanity: a degenerate/nonsensical fit (near-zero axis, or wildly off
    # from the rough radius) isn't better than the plain-circle fallback
    if a < rough_r * 0.6 or b < rough_r * 0.6 or a > rough_r * 1.3:
        return {"cx": cx, "cy": cy, "a": rough_r, "b": rough_r, "angle_deg": 0.0}
    return {"cx": ecx, "cy": ecy, "a": a, "b": b, "angle_deg": angle_deg}


def ellipse_point(ellipse: dict, board_angle_rad: float) -> tuple[float, float]:
    """Point on the fitted ellipse at a given board angle (0=top, clockwise) -
    the elliptical equivalent of the old (cx + r*sin, cy - r*cos) circle
    formula, additionally rotated by the ellipse's own fitted tilt."""
    ex = ellipse["a"] * math.sin(board_angle_rad)
    ey = -ellipse["b"] * math.cos(board_angle_rad)
    theta = math.radians(ellipse["angle_deg"])
    rx = ex * math.cos(theta) - ey * math.sin(theta)
    ry = ex * math.sin(theta) + ey * math.cos(theta)
    return ellipse["cx"] + rx, ellipse["cy"] + ry


def find_rotation(gray: np.ndarray, ellipse: dict) -> tuple[float, float]:
    """Fourier-phase rotation fit, sampled around the fitted ELLIPSE rather
    than a circle - sampling a circle on a board that's actually elliptical
    in-frame cuts across real wedge boundaries at an angle and smears the
    signal, weakening the fit. See PLAN.md for why Fourier phase beats
    thresholding for discrete edges in the first place."""
    n = 2880
    board_angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    h, w = gray.shape

    scales = [0.22, 0.28, 0.34, 0.40, 0.46]
    samples = np.zeros(n, dtype=np.float64)
    for s in scales:
        scaled = {**ellipse, "a": ellipse["a"] * s, "b": ellipse["b"] * s}
        pts = np.array([ellipse_point(scaled, a) for a in board_angles])
        xs = np.clip(pts[:, 0].astype(int), 0, w - 1)
        ys = np.clip(pts[:, 1].astype(int), 0, h - 1)
        samples += gray[ys, xs].astype(np.float64)
    samples /= len(scales)

    k = 10  # 2-wedge (36deg) color-repeat period -> 10 cycles/revolution
    coeff = np.sum(samples * np.exp(-1j * k * board_angles)) / n
    phase = np.angle(coeff)
    wedge = board_model.WEDGE
    boundary0 = (phase + np.pi / 2) / k
    boundary0 %= wedge
    strength = float(np.abs(coeff) / (np.abs(samples - samples.mean()).mean() + 1e-6))
    return float(boundary0), strength


def detect(bgr: np.ndarray) -> dict:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    ring_mask = _ring_mask(bgr)
    cx, cy = find_center(bgr, ring_mask)
    ellipse = find_outer_ellipse(bgr, ring_mask, cx, cy)
    rotation, strength = find_rotation(gray, ellipse)
    return {"cx": cx, "cy": cy, "ellipse": ellipse, "rotation": rotation, "fit_strength": strength}


def suggest_points(
    detection: dict,
    landmarks: tuple[int, ...],
    image_size: tuple[int, int],
) -> dict[str, tuple[float, float] | None]:
    """Seed the manual flow: center is trustworthy. Landmark positions are
    snapped to the nearest ACTUALLY-detected wedge center to an assumed
    upright orientation, then projected via the fitted ellipse - a real
    guess, not a random one, but the user should expect to nudge these, not
    just accept them blindly. Any point that would land outside the visible
    frame is returned as None (rather than placed somewhere the user can't
    see or drag it) - the manual flow just asks for it to be clicked."""
    w, h = image_size
    ellipse = detection["ellipse"]
    rotation = detection["rotation"]
    wedge = board_model.WEDGE
    detected_centers = [rotation + wedge / 2 + i * wedge for i in range(20)]

    def angular_diff(a: float, b: float) -> float:
        return abs(((a - b + math.pi) % (2 * math.pi)) - math.pi)

    def in_frame(x: float, y: float) -> bool:
        return 0 <= x <= w and 0 <= y <= h

    points: dict[str, tuple[float, float] | None] = {"center": (detection["cx"], detection["cy"])}
    for seg in landmarks:
        ideal_angle = board_model.segment_center_angle_rad(seg)
        best = min(detected_centers, key=lambda a: angular_diff(a, ideal_angle))
        x, y = ellipse_point(ellipse, best)
        points[str(seg)] = (float(x), float(y)) if in_frame(x, y) else None
    return points
