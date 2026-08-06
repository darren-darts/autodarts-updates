from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .base import Dart
from .engine import match_engine
from .registry import catalogue_view

router = APIRouter(prefix="/api/games", tags=["games"])


class StartIn(BaseModel):
    slug: str
    difficulty: str
    player_ids: list[str] | None = None
    options: dict | None = None


class ManualDartIn(BaseModel):
    """A dart entered by hand - for testing a game without throwing, and as
    the fallback when detection misses one."""
    segment: int | None = None
    multiplier: int = 1


def _label(segment: int | None, multiplier: int) -> str:
    if segment is None or multiplier == 0:
        return "MISS"
    if segment == 25:
        return "BULL" if multiplier == 2 else "25"
    return {1: "S", 2: "D", 3: "T"}[multiplier] + str(segment)


@router.get("/catalogue")
def catalogue():
    return {"games": catalogue_view()}


@router.get("/state")
def state():
    return match_engine.state()


@router.post("/start")
async def start(body: StartIn):
    loop = asyncio.get_running_loop()
    match_engine.bind_loop(loop)
    try:
        state = match_engine.start(body.slug, body.difficulty, body.player_ids, body.options)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Starting a game has to start the cameras too - otherwise the board
    # scores nothing and the only clue is that darts never appear. Reported
    # rather than raised: a game is still perfectly playable with the manual
    # keypad if the cameras aren't calibrated yet.
    from app import detection_manager

    detection = detection_manager.status()
    if not detection.get("active"):
        result = detection_manager.start(loop)
        state["detection"] = {
            "started": result.get("started", False),
            "error": result.get("error"),
            "camera_ids": result.get("camera_ids", []),
        }
    else:
        state["detection"] = {"started": True, "error": None,
                              "camera_ids": detection.get("camera_ids", [])}

    # Starting a game fills the main screen, whoever started it. A phone can't
    # call the browser's Fullscreen API on another machine, so this is the
    # CSS presentation layout - the screen still offers Restore.
    import display

    await display.set_presentation(True)
    state["display"] = display.state()
    return state


@router.post("/stop")
async def stop():
    match_engine.stop()
    # Give the screen its normal chrome back - you're heading to the library,
    # not watching a board.
    import display

    await display.set_presentation(False)
    return {"active": False, "display": display.state()}


@router.post("/next-turn")
def next_turn():
    return match_engine.next_turn()


@router.post("/confirm-takeout")
def confirm_takeout():
    """The Darts removed button. Overrides anything detection did since the
    "remove the darts" prompt appeared - see MatchEngine.confirm_takeout."""
    return match_engine.confirm_takeout()


@router.post("/previous-turn")
def previous_turn():
    """Go back a player. The recovery for a takeout that fired twice or early -
    see MatchEngine.previous_turn."""
    return match_engine.previous_turn()


@router.post("/undo")
def undo():
    return match_engine.undo_dart()


@router.post("/dart")
def manual_dart(body: ManualDartIn):
    if body.segment is not None and not (1 <= body.segment <= 20 or body.segment == 25):
        raise HTTPException(400, "segment must be 1-20, 25 for bull, or null for a miss")
    multiplier = body.multiplier
    if body.segment == 25:
        multiplier = 2 if multiplier >= 2 else 1
    score = 0 if body.segment is None else body.segment * multiplier
    dart = Dart(
        segment=body.segment,
        multiplier=0 if body.segment is None else multiplier,
        score=score,
        label=_label(body.segment, 0 if body.segment is None else multiplier),
    )
    return match_engine.submit_dart(dart)
