"""Typed records for the axis-based, multi-camera detection pipeline.

Ported from the reference implementation described in
DART_VISION_HANDOFF.md - see PLAN.md's detection section for why this
approach (fit a line per camera, intersect across cameras) replaced an
earlier single-camera "find the tip point" design.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AxisCandidate:
    camera_id: int
    image_line: tuple[float, float, float, float]  # x1, y1, x2, y2 in image px
    board_line: tuple[float, float, float]  # a, b, c of a*x + b*y + c = 0, board mm
    confidence: float
    changed_pixels: int
    line_pixels: int
    length_px: float
    elongation: float
    inlier_ratio: float
    noise_level: float
    threshold: float
    alignment_shift_px: tuple[float, float] = (0.0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PairIntersection:
    camera_ids: tuple[int, int]
    x_mm: float
    y_mm: float
    crossing_sine: float
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FusedHit:
    accepted: bool
    x_mm: float | None
    y_mm: float | None
    confidence: float
    residual_mm: float
    spread_mm: float
    cameras_used: list[int]
    label: str
    score: int
    segment: int | None
    multiplier: int
    reason: str
    review_required: bool = False
    base_confidence: float = 0.0
    confidence_bonus: float = 0.0
    positional_uncertainty_mm: float = 999.0
    wire_distance_mm: float = 0.0
    score_uncertain: bool = True
    intersections: list[PairIntersection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["intersections"] = [item.to_dict() for item in self.intersections]
        return payload
