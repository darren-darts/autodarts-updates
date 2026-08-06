"""Colour-driven calibration refinement.

The manual calibration solves a homography from exactly 5 clicked points, so
it is *exact* at those 5 points and free to drift everywhere else - a couple
of pixels of click error, or the lens distortion these wide-angle cameras
have, shows up as the projected grid sitting off the real wires on one side
of the board while fitting well on the other.

This module fixes that by measuring the board instead of trusting the clicks.
The double and treble rings are the only large red/green areas on the board
face, so they are a strong, unambiguous signal:

1. Build a "ring colour" mask (red or green, saturated).
2. Walk radially outward along each of the 20 segment centre lines, in board
   space through the current homography, and find where the treble and double
   bands actually start and end.
3. Each band's *midpoint* is a correspondence: the image pixel where the middle
   of the band really is, against the board millimetre radius where the spec
   says that midpoint should be.
4. Re-solve the homography from those ~40 well-spread correspondences with
   RANSAC, instead of the original 5 points.

Step 3 deliberately uses the band midpoint rather than its two edges. Each bed
is bounded by a metal wire that covers the true boundary, so the *coloured* run
reads narrower than the real bed by about a wire width at each end. Those two
errors are equal and opposite about the midpoint, so the midpoint is unbiased
while the edges are not - fitting to edges would have quietly tried to widen
every band and biased the board's apparent scale outward.

Because step 2 depends on the homography being refined, it runs a few times,
each pass re-sampling through the improved fit.

Measured on this rig, saturation is what separates the rings from the board:
the red/green bands sit at S~140-165 while the cream and black beds are
S~45-55 and the printed number ring is a different hue entirely. Hue is still
checked, because the board surround carries red decorative arcs and logo text
- the same contamination that skewed the original auto-detect ellipse fit.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import board_model

# Ring colour thresholds, measured from real frames on this rig (see the
# module docstring). Deliberately generous on hue and strict on saturation.
SAT_MIN = 90
VAL_MIN = 55
RED_LOW_MAX = 18        # hue <= this is red
RED_HIGH_MIN = 162      # hue >= this is also red (hue wraps)
GREEN_MIN, GREEN_MAX = 33, 98

# The two coloured bands we look for, as (inner_mm, outer_mm).
BANDS = (
    ("triple", board_model.RADII_MM["triple_in"], board_model.RADII_MM["triple_out"]),
    ("double", board_model.RADII_MM["double_in"], board_model.RADII_MM["double_out"]),
)

SEARCH_MM = 22.0        # how far either side of nominal to hunt for a band
STEP_MM = 0.4           # radial sampling resolution
MIN_BAND_MM = 3.0       # a plausible band is ~8mm; allow for perspective squash
MAX_BAND_MM = 18.0
PASSES = 3

MIN_CORRESPONDENCES = 20   # 20 segments x 2 bands is the full set, so this is half
MIN_INLIER_RATIO = 0.5
# Sanity bounds on the correction, so a bad fit can never be persisted.
MAX_CENTRE_SHIFT_PX = 80.0
MAX_SCALE_CHANGE = 0.25


def ring_mask(bgr: np.ndarray) -> np.ndarray:
    """True where a pixel is saturated red or green - i.e. double/treble bed."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    saturated = (sat >= SAT_MIN) & (val >= VAL_MIN)
    red = (hue <= RED_LOW_MAX) | (hue >= RED_HIGH_MIN)
    green = (hue >= GREEN_MIN) & (hue <= GREEN_MAX)
    mask = saturated & (red | green)
    # Close single-pixel gaps (wires, glare speckle) without moving edges.
    return cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)).astype(bool)


def _project(H_inv: np.ndarray, pts_mm) -> np.ndarray:
    arr = np.array(pts_mm, dtype=np.float32).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(arr, H_inv).reshape(-1, 2)


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True runs as (start_index, end_index_inclusive)."""
    out = []
    start = None
    for i, on in enumerate(flags):
        if on and start is None:
            start = i
        elif not on and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def _sample_ray(mask: np.ndarray, H_inv: np.ndarray, angle: float,
                inner_mm: float, outer_mm: float) -> tuple[float, float] | None:
    """Find where a coloured band really starts and ends along one segment's
    centre line. Returns the observed (inner_mm, outer_mm) radii, measured in
    the current calibration's board space."""
    centre = (inner_mm + outer_mm) / 2.0
    radii = np.arange(centre - SEARCH_MM, centre + SEARCH_MM + STEP_MM, STEP_MM)
    pts = [board_model.board_point_mm(angle, float(r)) for r in radii]
    px = _project(H_inv, pts)

    h, w = mask.shape
    xs = np.round(px[:, 0]).astype(int)
    ys = np.round(px[:, 1]).astype(int)
    inside = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    if not inside.all():
        return None   # ray leaves the frame; this segment can't be measured
    flags = mask[ys, xs]

    best = None
    for start, end in _runs(flags):
        # A run touching the window edge is clipped, so its edge is unknown.
        if start == 0 or end == len(flags) - 1:
            continue
        r0, r1 = float(radii[start]), float(radii[end])
        width = r1 - r0
        if not (MIN_BAND_MM <= width <= MAX_BAND_MM):
            continue
        offset = abs((r0 + r1) / 2.0 - centre)
        if best is None or offset < best[0]:
            best = (offset, r0, r1)
    if best is None:
        return None
    return best[1], best[2]


def measure(bgr: np.ndarray, H: np.ndarray) -> dict:
    """Where each coloured band's midpoint really sits, per segment, under the
    given homography. Reported as (angle, nominal_mid_mm, observed_mid_mm)."""
    mask = ring_mask(bgr)
    H_inv = np.linalg.inv(H).astype(np.float32)
    bands = []
    for index in range(20):
        angle = index * board_model.WEDGE
        for _name, inner, outer in BANDS:
            found = _sample_ray(mask, H_inv, angle, inner, outer)
            if found is None:
                continue
            bands.append((angle, (inner + outer) / 2.0, (found[0] + found[1]) / 2.0))
    errors = [abs(observed - nominal) for _a, nominal, observed in bands]
    return {
        "bands": bands,
        "mask": mask,
        "count": len(bands),
        "mean_error_mm": float(np.mean(errors)) if errors else None,
        "max_error_mm": float(np.max(errors)) if errors else None,
    }


def _segment_errors(bands) -> list[dict]:
    """Worst-offending segments, for the UI to report."""
    by_segment: dict[int, list[float]] = {}
    for angle, nominal, observed in bands:
        index = int(round(angle / board_model.WEDGE)) % 20
        by_segment.setdefault(board_model.SEGMENTS[index], []).append(abs(observed - nominal))
    return sorted(
        ({"segment": s, "error_mm": round(float(np.mean(v)), 2)} for s, v in by_segment.items()),
        key=lambda item: -item["error_mm"],
    )


def refine(bgr: np.ndarray, homography) -> dict:
    """Refine a calibration against the board's own red/green rings.

    Returns a report; `homography` in the result is None when the fit could
    not be trusted, and `reason` says why.
    """
    H0 = np.array(homography, dtype=np.float64)
    before = measure(bgr, H0)
    if before["count"] < MIN_CORRESPONDENCES:
        return {
            "improved": False, "homography": None,
            "reason": (f"only found {before['count']} ring edges (need {MIN_CORRESPONDENCES}). "
                       "Check the board is lit and unobstructed, and that the current "
                       "calibration is roughly right before fine-tuning."),
            "before_mm": before["mean_error_mm"], "after_mm": None, "samples": before["count"],
        }

    H = H0.copy()
    inliers_used = 0
    for _pass in range(PASSES):
        found = measure(bgr, H)
        if found["count"] < MIN_CORRESPONDENCES:
            break
        H_inv = np.linalg.inv(H).astype(np.float32)
        src_px, dst_mm = [], []
        for angle, nominal, observed in found["bands"]:
            [pixel] = _project(H_inv, [board_model.board_point_mm(angle, observed)])
            src_px.append(pixel)
            dst_mm.append(board_model.board_point_mm(angle, nominal))
        src = np.array(src_px, dtype=np.float32).reshape(-1, 1, 2)
        dst = np.array(dst_mm, dtype=np.float32).reshape(-1, 1, 2)
        candidate, inlier_flags = cv2.findHomography(src, dst, cv2.RANSAC, 2.5)
        if candidate is None:
            break
        ratio = float(inlier_flags.mean()) if inlier_flags is not None else 0.0
        if ratio < MIN_INLIER_RATIO:
            break
        inliers_used = int(inlier_flags.sum())
        H = candidate

    after = measure(bgr, H)
    report = {
        "samples": before["count"],
        "inliers": inliers_used,
        "before_mm": round(before["mean_error_mm"], 2) if before["mean_error_mm"] else None,
        "after_mm": round(after["mean_error_mm"], 2) if after["mean_error_mm"] else None,
        "before_max_mm": round(before["max_error_mm"], 2) if before["max_error_mm"] else None,
        "after_max_mm": round(after["max_error_mm"], 2) if after["max_error_mm"] else None,
        "worst_segments": _segment_errors(after["bands"])[:5],
    }

    if after["mean_error_mm"] is None or after["count"] < MIN_CORRESPONDENCES:
        return {**report, "improved": False, "homography": None,
                "reason": "the refined fit could not be re-measured reliably"}

    # Guard against a wild solution: the board centre must not jump, and the
    # apparent scale must not balloon. A bad fine-tune is worse than none.
    h, w = bgr.shape[:2]
    old_centre = _project(np.linalg.inv(H0).astype(np.float32), [(0.0, 0.0)])[0]
    new_centre = _project(np.linalg.inv(H).astype(np.float32), [(0.0, 0.0)])[0]
    shift = float(np.hypot(*(new_centre - old_centre)))
    if shift > MAX_CENTRE_SHIFT_PX:
        return {**report, "improved": False, "homography": None,
                "reason": f"rejected: the fit moved the board centre {shift:.0f}px, which is implausible"}

    def scale_of(matrix):
        pts = _project(np.linalg.inv(matrix).astype(np.float32),
                       [(0.0, 0.0), (board_model.RADII_MM["double_out"], 0.0)])
        return float(np.hypot(*(pts[1] - pts[0])))

    old_scale, new_scale = scale_of(H0), scale_of(H)
    if old_scale <= 0 or abs(new_scale - old_scale) / old_scale > MAX_SCALE_CHANGE:
        return {**report, "improved": False, "homography": None,
                "reason": "rejected: the fit changed the board's apparent size implausibly"}

    if after["mean_error_mm"] >= before["mean_error_mm"]:
        return {**report, "improved": False, "homography": None,
                "reason": (f"already well aligned - fine-tuning did not improve on "
                           f"{before['mean_error_mm']:.2f}mm mean ring error")}

    return {**report, "improved": True, "homography": H.tolist(), "reason": None}
