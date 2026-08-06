"""Multi-camera axis fusion: intersect 2-3 cameras' independently-fitted
dart-axis lines to recover the exact landing point, with confidence,
outlier handling and an accept/review-required/reject decision.

Ported from the reference implementation in DART_VISION_HANDOFF.md. This
is the piece the earlier single-camera pipeline never had - every score
used to come from one camera's unverified guess. See PLAN.md.
"""
from __future__ import annotations

import itertools
import math

import numpy as np

from calibration.board_model import PHYSICAL_BOARD_RADIUS_MM
from detection.scoring import score_board_point
from .models import AxisCandidate, FusedHit, PairIntersection

STRICT_MAX_RESIDUAL_MM = 5.0
STRICT_MAX_SPREAD_MM = 14.0
REVIEW_MAX_RESIDUAL_MM = 12.0
REVIEW_MAX_SPREAD_MM = 40.0
REVIEW_MIN_CAMERA_CONFIDENCE = 0.75
THREE_CAMERA_AUTO_ACCEPT_CONFIDENCE = 0.25
TWO_CAMERA_AUTO_ACCEPT_CONFIDENCE = 0.52
MAX_WIRE_SAFETY_BONUS = 0.25
FULL_WIRE_SAFETY_MARGIN_MM = 20.0
# A three-line least-squares solution can be destroyed by one camera whose
# calibration has moved, even when the other two axes make a clean on-board
# crossing. Only recover that pair when it is geometrically unique: every
# alternative crossing must be well outside the physical board. The result
# is deliberately provisional, never auto-accepted.
OUTLIER_PAIR_MIN_CAMERA_CONFIDENCE = 0.80
OUTLIER_PAIR_MIN_COMPOSITE_CONFIDENCE = 0.62
OUTLIER_PAIR_MIN_CROSSING_SINE = 0.25
OUTLIER_PAIR_MIN_THIRD_AXIS_ERROR_MM = 18.0
OUTLIER_PAIR_EXCLUSION_MARGIN_MM = 24.0


def _distance_to_scoring_wire(x_mm: float, y_mm: float) -> float:
    from calibration.board_model import distance_to_scoring_wire

    return distance_to_scoring_wire(x_mm, y_mm)


def _positional_uncertainty(
    usable: list[AxisCandidate],
    lines: np.ndarray,
    residual_mm: float,
    spread_mm: float,
) -> float:
    """Estimate a conservative board-plane error radius for score ambiguity.

    Two perfectly intersecting mathematical lines otherwise report zero
    residual and spread, so camera-axis confidence and intersection geometry
    must provide a non-zero floor.
    """
    axis_sigmas = np.array(
        [1.0 + 7.0 * (1.0 - float(np.clip(item.confidence, 0.0, 1.0))) ** 1.5 for item in usable],
        dtype=np.float64,
    )
    information = np.zeros((2, 2), dtype=np.float64)
    for normal, sigma in zip(lines, axis_sigmas, strict=True):
        information += np.outer(normal, normal) / max(float(sigma * sigma), 1e-6)
    try:
        covariance = np.linalg.inv(information)
        geometric_radius = 1.65 * math.sqrt(max(float(np.max(np.linalg.eigvalsh(covariance))), 0.0))
    except np.linalg.LinAlgError:
        geometric_radius = 30.0
    disagreement_radius = math.hypot(1.25 * residual_mm, 0.45 * spread_mm)
    camera_floor = 1.25 if len(usable) >= 3 else 2.0
    return float(np.clip(math.hypot(max(geometric_radius, camera_floor), disagreement_radius), 0.5, 60.0))


# Roughly 1.4x the ~3.5mm mean per-camera line error measured on real
# corrected throws: lines that miss the emerging consensus by more than this
# stop steering it. Measured behaviour is a broad plateau (3-8mm all behave
# the same), so this is not a knife-edge value.
ROBUST_TUNING_MM = 5.0
ROBUST_ITERATIONS = 6
# Robust reweighting needs enough redundancy to tell WHICH line is the outlier,
# and fitting a point (2 unknowns) to N lines only has N-2 spare observations.
# With three cameras that is exactly one, which is not enough - see
# _robust_refine's docstring for the demonstration. Four lines is the point at
# which a disagreeing camera can actually be identified rather than guessed at,
# so the reweighting is held back until then.
ROBUST_MIN_LINES = 4


def _robust_refine(
    lines: np.ndarray, targets: np.ndarray, weights: np.ndarray, solution: np.ndarray
) -> np.ndarray:
    """Re-solve, letting axes that disagree with the consensus lose influence.

    Plain least squares lets one bad axis drag the answer, so Tukey biweight
    reweighting pulls the solution towards whichever lines actually agree
    rather than averaging in an outlier. Falls back to the input solution if
    reweighting collapses (all lines rejected).

    ONLY VALID WITH FOUR OR MORE LINES (see ROBUST_MIN_LINES), which today's
    three-camera rig never reaches. With exactly three lines this does real
    damage, and the reason is structural rather than a matter of tuning:

    Take three lines through a common point and displace exactly one of them
    sideways. Whichever one you displace, the resulting pattern of "how far is
    each line from where the other two cross" comes out *identical* up to
    scale - for every camera layout, evenly spread or not. So the residuals
    the biweight iterates on contain no information about which line moved.
    What it actually converges on is a pair, chosen by whichever cameras carry
    the larger confidence weights, and axis confidence measures pixel count,
    length and elongation (see axis.py) - none of which say whether the line
    points in the right place.

    That is not hypothetical. A dart in the 25 was scored S3 because camera 2's
    axis was ~6.4mm out; cameras 1 and 3 crossed inside the bull, but camera 1
    sat 6.34mm from the camera-2/camera-3 crossing, over ROBUST_TUNING_MM, so
    the biweight drove its weight to zero and locked onto the two lines that
    happened to be more confident. Plain weighted least squares averages the
    two redundant views instead and lands inside the ring. On the replay
    benchmark (tools/detection_bench.py) holding this back takes segment
    accuracy from 4/7 to 6/7 with no case getting worse.

    The earlier evidence for enabling it on three cameras was three throws, and
    the case for it there is now understood to have been luck. It is kept, not
    deleted, because the argument above genuinely stops applying at four
    cameras - and because a broken camera still needs handling, which is what
    the evidence-gated OUTLIER_PAIR_* recovery path below is for.

    If a fourth camera is ever added, re-derive ROBUST_TUNING_MM before relying
    on this: a fixed 5mm cut-off is small next to the residuals a four-line
    starting solution can carry, and everything then gets rejected at once. The
    guard below makes that degrade to plain least squares rather than to
    garbage, but degrading is not the same as working.
    """
    current = solution
    active = weights.copy()
    for _ in range(ROBUST_ITERATIONS):
        residuals = np.abs(lines @ current - targets)
        scaled = np.clip(residuals / ROBUST_TUNING_MM, 0.0, 1.0)
        active = weights * (1.0 - scaled ** 2) ** 2
        if not np.any(active > 1e-9):
            return current
        root = np.sqrt(active)
        # Fewer than two independent surviving lines cannot pin a point down,
        # and lstsq does not say so - it returns the minimum-norm solution
        # ALONG the one remaining line, which lands wherever that line happens
        # to pass closest to the origin. Seen putting a treble 20 79mm away.
        # Keep the previous estimate instead of "refining" into nonsense.
        singular_values = np.linalg.svd(lines * root[:, None], compute_uv=False)
        if singular_values.size < 2 or singular_values[-1] <= 1e-6 * max(float(singular_values[0]), 1e-12):
            return current
        try:
            current = np.linalg.lstsq(lines * root[:, None], targets * root, rcond=None)[0]
        except np.linalg.LinAlgError:
            return current
    return current if np.isfinite(current).all() else solution


def _intersection(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float] | None:
    a1, b1, c1 = first
    a2, b2, c2 = second
    determinant = a1 * b2 - a2 * b1
    crossing_sine = abs(float(determinant))
    if crossing_sine < 0.10:
        return None
    x = (b1 * c2 - b2 * c1) / determinant
    y = (c1 * a2 - c2 * a1) / determinant
    if not np.isfinite([x, y]).all():
        return None
    return float(x), float(y), crossing_sine


def _has_strong_axis_evidence(candidate: AxisCandidate) -> bool:
    """Guard pair recovery with evidence produced by the axis detector.

    Confidence alone is not enough: a confidently fitted blob or a large
    frame-registration jump must not nominate a camera as the trusted pair.
    """
    shift_px = math.hypot(*candidate.alignment_shift_px)
    return (
        candidate.confidence >= OUTLIER_PAIR_MIN_CAMERA_CONFIDENCE
        and candidate.inlier_ratio >= 0.20
        and candidate.elongation >= 4.0
        and candidate.line_pixels >= 120
        and candidate.length_px >= 50.0
        and shift_px <= 3.0
    )


def _unique_on_board_pair_review(
    usable: list[AxisCandidate],
    raw_pair_intersections: list[tuple[AxisCandidate, AxisCandidate, PairIntersection]],
) -> FusedHit | None:
    """Return a provisional two-camera result for an unambiguous outlier.

    With three arbitrary lines there is no general way to know which pair is
    correct. Recovery is therefore allowed only when exactly one pair crosses
    the physical board and all other finite crossings miss it by a generous
    margin. Ambiguous three-way disagreement remains rejected.
    """
    if len(usable) != 3:
        return None

    on_board = [
        item
        for item in raw_pair_intersections
        if math.hypot(item[2].x_mm, item[2].y_mm) <= PHYSICAL_BOARD_RADIUS_MM
    ]
    if len(on_board) != 1:
        return None

    first, second, intersection = on_board[0]
    if (
        intersection.crossing_sine < OUTLIER_PAIR_MIN_CROSSING_SINE
        or not _has_strong_axis_evidence(first)
        or not _has_strong_axis_evidence(second)
    ):
        return None

    trusted_ids = {first.camera_id, second.camera_id}
    alternate_crossings = [
        item[2]
        for item in raw_pair_intersections
        if set(item[2].camera_ids) != trusted_ids
    ]
    exclusion_radius = PHYSICAL_BOARD_RADIUS_MM + OUTLIER_PAIR_EXCLUSION_MARGIN_MM
    if any(math.hypot(item.x_mm, item.y_mm) < exclusion_radius for item in alternate_crossings):
        return None

    third = next((item for item in usable if item.camera_id not in trusted_ids), None)
    if third is None:
        return None
    third_axis_error = abs(
        third.board_line[0] * intersection.x_mm
        + third.board_line[1] * intersection.y_mm
        + third.board_line[2]
    )
    if third_axis_error < OUTLIER_PAIR_MIN_THIRD_AXIS_ERROR_MM:
        return None

    # Reuse the normal strict two-camera calculation, then force review. This
    # preserves its geometry/uncertainty bookkeeping without a second
    # confidence formula for the exceptional path.
    pair_hit = fuse_axes([first, second])
    if (
        not pair_hit.accepted
        or pair_hit.confidence < OUTLIER_PAIR_MIN_COMPOSITE_CONFIDENCE
        or pair_hit.x_mm is None
        or pair_hit.y_mm is None
    ):
        return None
    pair_hit.accepted = False
    pair_hit.review_required = True
    pair_hit.reason = (
        f"camera {first.camera_id} and camera {second.camera_id} agree on the physical board while "
        f"camera {third.camera_id} is an outlier; score entered provisionally for confirmation"
    )
    return pair_hit


def fuse_axes(candidates: list[AxisCandidate]) -> FusedHit:
    usable = [item for item in candidates if item.confidence >= 0.22]
    if len(usable) < 2:
        return FusedHit(
            accepted=False,
            x_mm=None,
            y_mm=None,
            confidence=0.0,
            residual_mm=999.0,
            spread_mm=999.0,
            cameras_used=[item.camera_id for item in usable],
            label="NO HIT",
            score=0,
            segment=None,
            multiplier=0,
            reason="at least two camera axes are required",
        )

    if len(usable) >= 3:
        confidences = np.array([item.confidence for item in usable], dtype=np.float64)
        weakest = int(np.argmin(confidences))
        median = float(np.median(confidences))
        if confidences[weakest] < median * 0.62:
            usable.pop(weakest)

    pair_intersections: list[PairIntersection] = []
    raw_pair_intersections: list[tuple[AxisCandidate, AxisCandidate, PairIntersection]] = []
    for first, second in itertools.combinations(usable, 2):
        result = _intersection(first.board_line, second.board_line)
        if result is None:
            continue
        x_mm, y_mm, crossing_sine = result
        weight = math.sqrt(first.confidence * second.confidence) * crossing_sine
        intersection = PairIntersection(
            camera_ids=(first.camera_id, second.camera_id),
            x_mm=x_mm,
            y_mm=y_mm,
            crossing_sine=crossing_sine,
            weight=weight,
        )
        raw_pair_intersections.append((first, second, intersection))
        if math.hypot(x_mm, y_mm) <= 260.0:
            pair_intersections.append(intersection)

    if not pair_intersections:
        return FusedHit(
            accepted=False,
            x_mm=None,
            y_mm=None,
            confidence=0.0,
            residual_mm=999.0,
            spread_mm=999.0,
            cameras_used=[item.camera_id for item in usable],
            label="NO HIT",
            score=0,
            segment=None,
            multiplier=0,
            reason="camera axes are parallel or intersect outside the board area",
        )

    lines = np.array([[item.board_line[0], item.board_line[1]] for item in usable], dtype=np.float64)
    targets = -np.array([item.board_line[2] for item in usable], dtype=np.float64)
    weights = np.array([max(item.confidence, 0.05) for item in usable], dtype=np.float64)
    weighted_lines = lines * np.sqrt(weights[:, None])
    weighted_targets = targets * np.sqrt(weights)
    try:
        solution, _, _, singular = np.linalg.lstsq(weighted_lines, weighted_targets, rcond=None)
        if len(usable) >= ROBUST_MIN_LINES:
            solution = _robust_refine(lines, targets, weights, solution)
    except np.linalg.LinAlgError:
        solution = np.array([pair_intersections[0].x_mm, pair_intersections[0].y_mm], dtype=np.float64)
        singular = np.array([1.0, 0.0])
    x_mm, y_mm = float(solution[0]), float(solution[1])

    line_residuals = np.abs(lines @ solution - targets)
    residual_mm = float(np.average(line_residuals, weights=weights))
    pair_points = np.array([[item.x_mm, item.y_mm] for item in pair_intersections], dtype=np.float64)
    pair_weights = np.array([max(item.weight, 0.01) for item in pair_intersections], dtype=np.float64)
    if len(pair_points) > 1:
        pair_center = np.average(pair_points, axis=0, weights=pair_weights)
        spread_mm = float(np.sqrt(np.average(np.sum((pair_points - pair_center) ** 2, axis=1), weights=pair_weights)))
    else:
        spread_mm = 0.0

    average_camera_confidence = float(np.average([item.confidence for item in usable], weights=weights))
    geometry_score = float(np.average([item.crossing_sine for item in pair_intersections], weights=pair_weights))
    agreement_score = math.exp(-residual_mm / 3.5) * math.exp(-spread_mm / 10.0)
    conditioning = float(singular[-1] / singular[0]) if len(singular) >= 2 and singular[0] > 1e-9 else 0.0
    condition_score = min(1.0, conditioning / 0.20)
    camera_factor = 1.0 if len(usable) >= 3 else 0.88
    base_confidence = float(np.clip(
        average_camera_confidence
        * (0.35 + 0.65 * math.sqrt(max(geometry_score, 0.0)))
        * (0.65 + 0.35 * condition_score)
        * agreement_score
        * camera_factor,
        0.0,
        0.99,
    ))

    scored = score_board_point(x_mm, y_mm)
    positional_uncertainty_mm = _positional_uncertainty(usable, lines, residual_mm, spread_mm)
    wire_distance_mm = _distance_to_scoring_wire(x_mm, y_mm)
    safe_margin_mm = max(0.0, wire_distance_mm - positional_uncertainty_mm)
    confidence_bonus = MAX_WIRE_SAFETY_BONUS * min(1.0, safe_margin_mm / FULL_WIRE_SAFETY_MARGIN_MM)
    confidence = float(np.clip(base_confidence + confidence_bonus, 0.0, 0.99))
    score_uncertain = wire_distance_mm <= positional_uncertainty_mm
    geometry_within_limits = residual_mm <= STRICT_MAX_RESIDUAL_MM and spread_mm <= STRICT_MAX_SPREAD_MM
    guarded_disagreement_review = (
        len(usable) >= 3
        and len(pair_intersections) >= 2
        and min(item.confidence for item in usable) >= REVIEW_MIN_CAMERA_CONFIDENCE
        and residual_mm <= REVIEW_MAX_RESIDUAL_MM
        and spread_mm <= REVIEW_MAX_SPREAD_MM
        and math.hypot(x_mm, y_mm) <= 235.0
    )
    normal_confidence_threshold = (
        THREE_CAMERA_AUTO_ACCEPT_CONFIDENCE if len(usable) >= 3 else TWO_CAMERA_AUTO_ACCEPT_CONFIDENCE
    )
    normal_confidence = confidence >= normal_confidence_threshold
    strong_three_camera_consensus = (
        len(usable) >= 3
        and min(item.confidence for item in usable) >= 0.70
        and confidence >= 0.36
        and residual_mm <= 2.0
        and spread_mm <= 4.0
    )
    accepted = geometry_within_limits and (normal_confidence or strong_three_camera_consensus)
    existing_review_candidate = not accepted and (geometry_within_limits or guarded_disagreement_review)
    review_required = existing_review_candidate and score_uncertain
    score_certain_accept = existing_review_candidate and not score_uncertain
    accepted = accepted or score_certain_accept
    if not accepted:
        pair_review = _unique_on_board_pair_review(usable, raw_pair_intersections)
        if pair_review is not None:
            return pair_review
    if accepted:
        reason = (
            "strong three-camera consensus accepted"
            if strong_three_camera_consensus
            else "score boundary is clear despite positional uncertainty"
            if score_certain_accept
            else "multicamera geometry accepted"
        )
    elif guarded_disagreement_review:
        reason = "strong dart axes disagree moderately; score entered provisionally for confirmation"
    elif review_required:
        reason = "low-confidence geometry scored provisionally; confirmation required"
    elif residual_mm > STRICT_MAX_RESIDUAL_MM or spread_mm > STRICT_MAX_SPREAD_MM:
        reason = "camera axes disagree; review recorded event"
    else:
        reason = "geometry confidence is below the guarded acceptance threshold"

    return FusedHit(
        accepted=accepted,
        x_mm=x_mm,
        y_mm=y_mm,
        confidence=confidence,
        residual_mm=residual_mm,
        spread_mm=spread_mm,
        cameras_used=[item.camera_id for item in usable],
        label=_label_for(scored),
        score=int(scored["value"]),
        segment=scored["segment"],
        multiplier=int(scored["multiplier"]),
        reason=reason,
        review_required=review_required,
        base_confidence=base_confidence,
        confidence_bonus=confidence_bonus,
        positional_uncertainty_mm=positional_uncertainty_mm,
        wire_distance_mm=wire_distance_mm,
        score_uncertain=score_uncertain,
        intersections=pair_intersections,
    )


def _label_for(scored: dict) -> str:
    ring = scored.get("ring")
    if ring == "bullseye":
        return "BULL"
    if ring == "outer_bull":
        return "25"
    if ring == "out":
        return "OUT"
    prefix = {"single": "S", "triple": "T", "double": "D"}.get(ring, "")
    return f"{prefix}{scored['segment']}"
