"""Per-camera dart-axis detection: instead of guessing which end of an
elongated silhouette is "the tip" (unreliable in one oblique 2D view - see
PLAN.md), fit the dart's whole AXIS as a line. The tip-ambiguity problem is
solved later, at fusion time, by intersecting this line with another
camera's line for the same dart - both are guaranteed to pass through the
true landing point even though neither can locate it alone.

Ported from the reference implementation in DART_VISION_HANDOFF.md.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from calibration import board_model
from .models import AxisCandidate


def as_homography(value: Iterable[Iterable[float]]) -> np.ndarray:
    matrix = np.asarray(value, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("homography must be a finite 3x3 matrix")
    if abs(float(np.linalg.det(matrix))) < 1e-12:
        raise ValueError("homography is singular")
    return matrix / matrix[2, 2]


def transform_points(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    source = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(source, matrix).reshape(-1, 2)


def line_from_points(point_a: Iterable[float], point_b: Iterable[float]) -> tuple[float, float, float]:
    a = np.array([*point_a, 1.0], dtype=np.float64)
    b = np.array([*point_b, 1.0], dtype=np.float64)
    line = np.cross(a, b)
    norm = float(np.hypot(line[0], line[1]))
    if norm < 1e-9:
        raise ValueError("line points are coincident")
    line /= norm
    return float(line[0]), float(line[1]), float(line[2])


def transform_image_line_to_board(
    image_to_board: np.ndarray,
    image_line: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    points = np.array([[image_line[0], image_line[1]], [image_line[2], image_line[3]]], dtype=np.float64)
    board_points = transform_points(image_to_board, points)
    return line_from_points(board_points[0], board_points[1])


def board_mask(shape: tuple[int, int], board_to_image: np.ndarray, margin_mm: float) -> np.ndarray:
    """Image-space mask of the physical board face (scoring radius + margin),
    used to keep motion/diff analysis restricted to the board itself."""
    height, width = shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    radius = board_model.SCORING_RADIUS_MM + margin_mm
    angles = np.linspace(0.0, 2 * math.pi, 240, endpoint=True)
    outline = np.array([board_model.board_point_mm(a, radius) for a in angles], dtype=np.float64)
    projected = transform_points(board_to_image, outline)
    if np.isfinite(projected).all():
        polygon = np.rint(projected).astype(np.int32)
        cv2.fillPoly(mask, [polygon], 255)
    return mask


@dataclass
class AxisAnalysis:
    candidate: AxisCandidate | None
    aligned_post: np.ndarray
    difference: np.ndarray
    mask: np.ndarray
    reason: str
    changed_ratio: float


def _normalized_gray(frame: np.ndarray, roi: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    values = gray[roi > 0]
    if values.size:
        median = float(np.median(values))
        deviation = float(np.percentile(values, 75) - np.percentile(values, 25))
        gray = (gray - median) / max(deviation, 24.0) * 42.0 + 128.0
    return np.clip(gray, 0, 255).astype(np.uint8)


def _align_post_to_pre(pre: np.ndarray, post: np.ndarray, roi: np.ndarray) -> tuple[np.ndarray, tuple[float, float]]:
    x, y, width, height = cv2.boundingRect(roi)
    if width < 40 or height < 40:
        return post.copy(), (0.0, 0.0)
    pre_gray = cv2.cvtColor(pre[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY).astype(np.float32)
    post_gray = cv2.cvtColor(post[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY).astype(np.float32)
    pre_gray = cv2.GaussianBlur(pre_gray, (5, 5), 0)
    post_gray = cv2.GaussianBlur(post_gray, (5, 5), 0)
    window = cv2.createHanningWindow((width, height), cv2.CV_32F)
    try:
        (dx, dy), response = cv2.phaseCorrelate(pre_gray, post_gray, window)
    except cv2.error:
        return post.copy(), (0.0, 0.0)
    if not np.isfinite([dx, dy, response]).all() or response < 0.04 or math.hypot(dx, dy) > 8.0:
        return post.copy(), (0.0, 0.0)
    transform = np.array([[1.0, 0.0, -dx], [0.0, 1.0, -dy]], dtype=np.float32)
    aligned = cv2.warpAffine(post, transform, (post.shape[1], post.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return aligned, (float(dx), float(dy))


def _remove_small_components(mask: np.ndarray, minimum_area: int = 10) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area >= minimum_area:
            cleaned[labels == label] = 255
    return cleaned


SUPPORT_GAP_PX = 25.0
# Rejecting an axis because its support has a gap was a mistake, kept here as
# a disabled knob rather than deleted so the reasoning survives.
#
# It was introduced from a single failing throw where RANSAC bridged a dart
# flight and a 24px speck of noise. Re-measured later against the user's
# corrected throws it turned out to have NO relationship to accuracy: a 79%
# gap produced a 2.20mm line (the best camera of that throw) while a 15% gap
# produced a 7.74mm one, and it was discarding the single most accurate
# camera on two of three throws - which then forced a two-camera solve and
# amplified the error. The bridging problem itself is fixed at source by
# scoring _contiguous_extent below, so the gate was pure loss.
MAX_SUPPORT_GAP_RATIO = float("inf")


def _contiguous_extent(projections: np.ndarray, max_gap_px: float = SUPPORT_GAP_PX) -> float:
    """Longest run of support along the axis containing no gap wider than
    max_gap_px.

    Plain extent is actively harmful as a score: a line bridging a dart
    flight and an unrelated speck of noise 450px away measures *longer*
    than the dart itself, so RANSAC prefers it. Scoring only the connected
    run removes that incentive - a real dart is one continuous object."""
    if projections.size == 0:
        return 0.0
    ordered = np.sort(projections)
    breaks = np.flatnonzero(np.diff(ordered) > max_gap_px)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [ordered.size - 1]))
    return float(np.max(ordered[ends] - ordered[starts]))


def _support_gap_ratio(support: np.ndarray, center: np.ndarray, direction: np.ndarray) -> float:
    """Largest gap along the fitted axis as a fraction of its total span.
    Near 0 for a real dart (one contiguous silhouette); near 1 when the
    fit has bridged two disconnected blobs."""
    projections = np.sort((support - center) @ direction)
    if projections.size < 2:
        return 1.0
    span = float(projections[-1] - projections[0])
    if span <= 1e-6:
        return 1.0
    return float(np.max(np.diff(projections)) / span)


def _ransac_axis(
    points: np.ndarray, iterations: int = 320
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float] | None:
    if len(points) < 20:
        return None
    rng = np.random.default_rng(20260710)
    sample_points = points
    if len(sample_points) > 6000:
        sample_points = sample_points[rng.choice(len(sample_points), 6000, replace=False)]

    best_inliers: np.ndarray | None = None
    best_score = -1.0
    for _ in range(iterations):
        indices = rng.choice(len(sample_points), 2, replace=False)
        first, second = sample_points[indices]
        vector = second - first
        length = float(np.linalg.norm(vector))
        if length < 18.0:
            continue
        direction = vector / length
        normal = np.array([-direction[1], direction[0]])
        distances = np.abs((sample_points - first) @ normal)
        inliers = distances <= 4.0
        inlier_count = int(np.count_nonzero(inliers))
        if inlier_count < 18:
            continue
        projections = (sample_points[inliers] - first) @ direction
        extent = _contiguous_extent(projections)
        score = inlier_count * min(extent, 260.0)
        if score > best_score:
            best_score = score
            best_inliers = inliers

    if best_inliers is None:
        return None
    core = sample_points[best_inliers].astype(np.float32)
    vx, vy, x0, y0 = cv2.fitLine(core, cv2.DIST_HUBER, 0, 0.01, 0.01).flatten()
    direction = np.array([float(vx), float(vy)], dtype=np.float64)
    direction /= max(float(np.linalg.norm(direction)), 1e-9)
    center = np.array([float(x0), float(y0)], dtype=np.float64)
    normal = np.array([-direction[1], direction[0]])
    distances = np.abs((points - center) @ normal)
    support = points[distances <= 6.0]
    if len(support) < 20:
        support = core.astype(np.float64)
    projections = (support - center) @ direction
    lower, upper = np.percentile(projections, [1.0, 99.0])
    endpoint_a = center + direction * lower
    endpoint_b = center + direction * upper
    gap_ratio = _support_gap_ratio(support, center, direction)
    return endpoint_a, endpoint_b, support, gap_ratio


def detect_dart_axis(
    camera_id: int,
    pre_frame: np.ndarray,
    post_frame: np.ndarray,
    image_to_board: np.ndarray | list[list[float]],
    board_to_image: np.ndarray | list[list[float]],
) -> AxisAnalysis:
    if pre_frame.shape != post_frame.shape:
        raise ValueError("pre and post frames must have the same shape")

    board_h = as_homography(board_to_image)
    inverse_h = as_homography(image_to_board)
    roi = board_mask(pre_frame.shape, board_h, margin_mm=board_model.DETECTION_MASK_MARGIN_MM)
    roi_pixels = max(int(np.count_nonzero(roi)), 1)
    aligned_post, alignment_shift = _align_post_to_pre(pre_frame, post_frame, roi)

    pre_gray = _normalized_gray(pre_frame, roi)
    post_gray = _normalized_gray(aligned_post, roi)
    difference = cv2.absdiff(pre_gray, post_gray)
    difference = cv2.GaussianBlur(difference, (3, 3), 0)
    values = difference[roi > 0]
    median = float(np.median(values)) if values.size else 0.0
    mad = float(np.median(np.abs(values.astype(np.float32) - median))) if values.size else 0.0
    noise_sigma = 1.4826 * mad
    threshold_value = float(np.clip(max(11.0, median + 5.5 * noise_sigma), 11.0, 55.0))
    _, mask = cv2.threshold(difference, threshold_value, 255, cv2.THRESH_BINARY)
    mask = cv2.bitwise_and(mask, roi)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    mask = _remove_small_components(mask, minimum_area=9)

    changed_pixels = int(np.count_nonzero(mask))
    changed_ratio = changed_pixels / roi_pixels

    if changed_pixels < 20:
        return AxisAnalysis(None, aligned_post, difference, mask, "no persistent dart-sized change", changed_ratio)
    if changed_ratio > 0.18:
        return AxisAnalysis(None, aligned_post, difference, mask, "large scene change or takeout", changed_ratio)

    y_values, x_values = np.nonzero(mask)
    points = np.column_stack((x_values, y_values)).astype(np.float64)
    axis = _ransac_axis(points)
    if axis is None:
        return AxisAnalysis(None, aligned_post, difference, mask, "changed pixels do not form a reliable axis", changed_ratio)

    endpoint_a, endpoint_b, support, gap_ratio = axis
    # A dart is one continuous object. A support cloud split by a large
    # gap means the fit has bridged two disconnected things - typically a
    # dart flight and a speck of noise, or two separate darts - and the
    # resulting axis is confidently wrong, which is far worse than no
    # answer at all: fusion can still solve from the other cameras.
    if gap_ratio > MAX_SUPPORT_GAP_RATIO:
        return AxisAnalysis(
            None, aligned_post, difference, mask,
            f"axis support is split by a {gap_ratio:.0%} gap - not one continuous dart",
            changed_ratio,
        )

    length_px = float(np.linalg.norm(endpoint_b - endpoint_a))
    centered = support - np.mean(support, axis=0)
    covariance = np.cov(centered.T) if len(support) > 2 else np.eye(2)
    eigenvalues = np.sort(np.maximum(np.linalg.eigvalsh(covariance), 1e-6))
    elongation = float(math.sqrt(eigenvalues[-1] / eigenvalues[0]))
    line_pixels = int(len(support))
    inlier_ratio = line_pixels / max(changed_pixels, 1)

    if length_px < 24.0 or elongation < 2.0:
        return AxisAnalysis(None, aligned_post, difference, mask, "foreground is not sufficiently dart-shaped", changed_ratio)

    image_line = (float(endpoint_a[0]), float(endpoint_a[1]), float(endpoint_b[0]), float(endpoint_b[1]))
    try:
        board_line = transform_image_line_to_board(inverse_h, image_line)
    except ValueError:
        return AxisAnalysis(None, aligned_post, difference, mask, "axis could not be mapped to the board", changed_ratio)

    support_score = min(1.0, line_pixels / 160.0)
    length_score = min(1.0, length_px / 110.0)
    inlier_score = min(1.0, inlier_ratio / 0.48)
    elongation_score = min(1.0, max(0.0, elongation - 1.5) / 8.0)
    noise_score = math.exp(-noise_sigma / 8.0)
    confidence = float(np.clip(
        0.22 * support_score
        + 0.26 * length_score
        + 0.24 * inlier_score
        + 0.18 * elongation_score
        + 0.10 * noise_score,
        0.0,
        0.98,
    ))

    candidate = AxisCandidate(
        camera_id=camera_id,
        image_line=image_line,
        board_line=board_line,
        confidence=confidence,
        changed_pixels=changed_pixels,
        line_pixels=line_pixels,
        length_px=length_px,
        elongation=elongation,
        inlier_ratio=inlier_ratio,
        noise_level=noise_sigma,
        threshold=threshold_value,
        alignment_shift_px=alignment_shift,
    )
    return AxisAnalysis(candidate, aligned_post, difference, mask, "axis detected", changed_ratio)


def analysis_summary(analysis: AxisAnalysis) -> dict[str, Any]:
    return {
        "candidate": analysis.candidate.to_dict() if analysis.candidate else None,
        "reason": analysis.reason,
        "changed_ratio": analysis.changed_ratio,
    }
