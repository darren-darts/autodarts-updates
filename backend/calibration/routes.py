from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import settings_store
from calibration import auto_detect, fine_tune, store

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


class PointsIn(BaseModel):
    points: dict[str, list[float]]


def _manager():
    # Lazy import: app.py imports this router at module load time, so a
    # top-level `from app import manager` would be circular.
    from app import manager

    return manager


@router.get("")
def get_all():
    return {"cameras": store.get_all(), "landmarks": list(store.LANDMARKS)}


@router.get("/{camera_id}")
def get_one(camera_id: int):
    grid = store.grid_for(camera_id)
    if grid is None:
        return {"camera_id": camera_id, "calibrated": False}
    return {"camera_id": camera_id, "calibrated": True, **grid}


@router.post("/{camera_id}/auto")
def auto(camera_id: int):
    """Detection only - does NOT save a calibration. The cameras look at
    the board from an angle, so a landmark's best-guess position can
    legitimately be outside the visible frame (returned as null) or just
    wrong; this seeds the manual flow, which is what actually persists a
    calibration once the user has confirmed/adjusted every point."""
    capture = settings_store.load()["capture"]
    try:
        bgr = _manager().grab_frame_bgr(
            camera_id, capture["width"], capture["height"], capture["fps"], timeout=10.0
        )
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    try:
        detection = auto_detect.detect(bgr)
    except RuntimeError as e:
        raise HTTPException(422, str(e))

    h, w = bgr.shape[:2]
    points = auto_detect.suggest_points(detection, store.LANDMARKS, (w, h))
    return {
        "camera_id": camera_id,
        "fit_strength": detection["fit_strength"],
        "points": {k: (list(v) if v is not None else None) for k, v in points.items()},
    }


@router.post("/{camera_id}/fine-tune")
def auto_fine_tune(camera_id: int):
    """Nudge an existing calibration onto the board's own double/treble rings
    using colour, rather than trusting the 5 clicked points.

    Unlike /auto this DOES persist - but only when it can show the fit got
    measurably better, and only after sanity checks; otherwise the existing
    calibration is left exactly as it was and the reason is returned.
    """
    profile = store.get(camera_id)
    if profile is None:
        raise HTTPException(400, "calibrate this camera first - fine-tuning refines an "
                                 "existing calibration, it cannot create one")

    capture = settings_store.load()["capture"]
    try:
        bgr = _manager().grab_frame_bgr(
            camera_id, capture["width"], capture["height"], capture["fps"], timeout=10.0
        )
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    result = fine_tune.refine(bgr, profile["homography"])
    response = {
        "camera_id": camera_id,
        "applied": False,
        "improved": result["improved"],
        "reason": result.get("reason"),
        "samples": result.get("samples"),
        "inliers": result.get("inliers"),
        "before_mm": result.get("before_mm"),
        "after_mm": result.get("after_mm"),
        "before_max_mm": result.get("before_max_mm"),
        "after_max_mm": result.get("after_max_mm"),
        "worst_segments": result.get("worst_segments", []),
    }
    if not result["improved"] or result["homography"] is None:
        return response

    store.save_homography(camera_id, result["homography"], source="fine-tuned")
    grid = store.grid_for(camera_id)
    return {**response, "applied": True, "calibrated": True, **grid}


@router.put("/{camera_id}/points")
def set_points(camera_id: int, body: PointsIn):
    try:
        store.save_points(camera_id, body.points, source="manual")
    except ValueError as e:
        raise HTTPException(400, str(e))
    grid = store.grid_for(camera_id)
    return {"camera_id": camera_id, "calibrated": True, **grid}


@router.delete("/{camera_id}")
def clear(camera_id: int):
    store.clear(camera_id)
    return {"camera_id": camera_id, "calibrated": False}
