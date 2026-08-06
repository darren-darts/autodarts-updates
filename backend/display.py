"""Main-screen display control - lets a phone drive the big screen.

The only state is a "presentation mode" flag: when on, the main screen's
play view hides the app chrome and fills the display, exactly as if
someone had pressed the fullscreen button on it. A phone can't call the
browser Fullscreen API on another machine, so this works by broadcasting
the flag over the shared WebSocket hub; the main screen reacts by
entering/leaving a CSS fullscreen layout and shows a restore button.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from events import hub

router = APIRouter(prefix="/api/display", tags=["display"])

_state = {"presentation": False}


class PresentationIn(BaseModel):
    enabled: bool


async def set_presentation(enabled: bool) -> dict:
    """Flip presentation mode and tell every connected screen.

    Callable from anywhere, not just the route - starting a game turns this
    on so the main screen fills itself without anyone having to walk over and
    press a button, and stopping one turns it back off.
    """
    _state["presentation"] = bool(enabled)
    await hub.broadcast({"type": "display.presentation", "enabled": _state["presentation"]})
    return dict(_state)


def state() -> dict:
    return dict(_state)


@router.get("")
def display_state():
    return state()


@router.post("/presentation")
async def set_presentation_route(body: PresentationIn):
    return await set_presentation(body.enabled)
