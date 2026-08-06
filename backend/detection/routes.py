from __future__ import annotations

import asyncio

import numpy as np
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, model_validator

from calibration import board_model
from detection import axis as axis_module

router = APIRouter(prefix="/api/detection", tags=["detection"])


class CorrectionIn(BaseModel):
    # Either a click on a camera's evidence photo (camera_id + x_px/y_px,
    # converted through that camera's own homography), or a click directly
    # on the front-on board diagram (x_mm/y_mm, already in board space) -
    # exactly one of the two modes, never a mix.
    camera_id: int | None = None
    x_px: float | None = None
    y_px: float | None = None
    x_mm: float | None = None
    y_mm: float | None = None

    @model_validator(mode="after")
    def _exactly_one_mode(self):
        from_camera = self.camera_id is not None and self.x_px is not None and self.y_px is not None
        from_board = self.x_mm is not None and self.y_mm is not None
        if from_camera == from_board:  # both or neither
            raise ValueError("provide either camera_id+x_px+y_px, or x_mm+y_mm, not both")
        return self


def _detection_manager():
    # Lazy import: app.py imports this router at module load time, so a
    # top-level `from app import detection_manager` would be circular.
    from app import detection_manager

    return detection_manager


@router.post("/start")
async def start():
    """Starts ONE session watching every configured, calibrated camera
    together - fusion needs at least 2 cameras to agree, so there's no
    longer a meaningful per-camera start/stop."""
    loop = asyncio.get_running_loop()
    result = _detection_manager().start(loop)
    if not result["started"]:
        raise HTTPException(400, result.get("error", "could not start detection"))
    return result


@router.post("/stop")
def stop():
    _detection_manager().stop()
    return {"active": False}


@router.get("/status")
def status():
    return _detection_manager().status()


@router.get("/history/{event_id}/frame/{camera_id}")
def evidence_frame(event_id: int, camera_id: int):
    """The actual frame a throw was scored from, so a correction can be
    placed on the dart as it really looked, not a live stream that's moved
    on since. Only kept for the most recent handful of events - see
    DetectionSession._evidence_max."""
    jpeg = _detection_manager().get_evidence_frame(event_id, camera_id)
    if jpeg is None:
        raise HTTPException(404, "no evidence frame for that event/camera (too old, or never captured)")
    return Response(content=jpeg, media_type="image/jpeg")


@router.post("/history/{event_id}/correct")
def correct(event_id: int, body: CorrectionIn):
    manager = _detection_manager()
    if body.x_mm is not None:
        entry = manager.apply_board_correction(event_id, body.x_mm, body.y_mm)
    else:
        entry = manager.apply_correction(event_id, body.camera_id, body.x_px, body.y_px)
    if entry is None:
        raise HTTPException(404, "event not found, or that camera isn't calibrated")
    return entry


@router.post("/trace/start")
def trace_start():
    """Begin recording every signal a takeout decision could use, at ~10Hz.

    Deliberately manual: it costs extra frame comparisons per camera, and the
    point is to capture a handful of real removals in detail rather than to run
    continuously. See DetectionSession._trace_sample.
    """
    result = _detection_manager().start_trace()
    if not result.get("tracing"):
        raise HTTPException(400, result.get("error", "could not start tracing"))
    return result


@router.post("/trace/stop")
def trace_stop():
    return _detection_manager().stop_trace()


@router.get("/trace")
def trace():
    return _detection_manager().trace_dump()


@router.get("/analyze")
def analyze():
    return _detection_manager().analyze()


@router.post("/project")
def project(body: dict):
    """Board mm -> each calibrated camera's image pixels, so the UI can draw
    where a fused hit lands in every camera view. Doing it server-side keeps
    the homographies in one place rather than shipping them to the client."""
    from calibration import store as calibration_store

    x_mm, y_mm = float(body["x_mm"]), float(body["y_mm"])
    point = np.array([[x_mm, y_mm]], dtype=np.float64)
    out: dict[int, list[float]] = {}
    for camera_id, profile in calibration_store.get_all().items():
        board_to_image = np.linalg.inv(np.array(profile["homography"], dtype=np.float64))
        px = axis_module.transform_points(board_to_image, point)[0]
        out[int(camera_id)] = [float(px[0]), float(px[1])]
    return {"points": out}


@router.get("/board-geometry")
def board_geometry():
    """Static dartboard geometry (radii + segment order), for drawing a
    front-on diagram client-side - the frontend computes wedge shapes
    itself from this rather than duplicating board_model's constants, so
    there's exactly one place segment order/radii can drift out of sync
    with actual scoring."""
    return {
        "physical_board_radius_mm": board_model.PHYSICAL_BOARD_RADIUS_MM,
        "radii_mm": board_model.RADII_MM,
        "segments": board_model.SEGMENTS,
    }
