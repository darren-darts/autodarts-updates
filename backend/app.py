"""Autodarts backend — FastAPI entry point.

Run from the backend/ directory:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import paths
import settings_store
from calibration import board_model
from detection.autodarts import AutodartsDetector
from display import router as display_router
from events import hub
from games.engine import match_engine
from games.routes import router as games_router
from leds.controller import led_controller
from leds.effects import EFFECTS
from leds.transport import TransportError, list_serial_ports
from network import list_lan_ips
from players.routes import router as players_router
from update import restart as update_restart
from update import routes as update_routes
from update.routes import router as update_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("autodarts")


def _on_autodarts_takeout() -> None:
    """Darts pulled out of the board = the visit is over.

    Mirrors the manual "Darts removed" button: fire the red takeout flash and
    advance the engine. next_turn broadcasts the new game.state, which is what
    clears the takeout prompt on every screen. cue=False so next_turn's own blue
    comet doesn't overwrite the red flash a few milliseconds later.
    """
    led_controller.flash_cue("takeout", duration_s=0.5)
    match_engine.next_turn(cue=False)
    # The play screen listens for this to play its takeout sound and the "darts
    # coming out" flash - same event the old CV takeout broadcast. We're on the
    # event loop here (called from the detector's async poll task), so schedule
    # the async broadcast rather than awaiting it.
    try:
        asyncio.get_running_loop().create_task(hub.broadcast({"type": "detection.takeout"}))
    except RuntimeError:
        pass


# Autodarts is now the detection source: it owns the cameras, calibration and
# dart localisation, and we consume scored darts from its local Board Manager
# API. The engine is fed exactly as the old CV pipeline fed it (submit_dart /
# next_turn), so no game knows the difference. Started/stopped in the lifespan.
autodarts_detector = AutodartsDetector(
    on_dart=match_engine.submit_dart,
    on_takeout_finished=_on_autodarts_takeout,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    paths.ensure_dirs()
    led_controller.start()
    # Also the state momentary cues (e.g. the dart-detected flash) fall back to.
    led_controller.set_resting_cue("startup")

    # Release the LED serial port before an update restart, which exits hard
    # rather than waiting on uvicorn's graceful shutdown (see update/restart.py).
    # Without this the new process would find the port still held by the old one.
    # (Cameras are Autodarts' concern now, not ours.)
    update_restart.set_shutdown_hook(_release_hardware)
    update_routes.start_background_check()

    # The engine broadcasts game.state from the detector's thread/task, so it
    # needs the running loop; bind it here rather than only when a game starts,
    # so a dart detected before the first /api/games/start still lands.
    match_engine.bind_loop(asyncio.get_running_loop())
    await autodarts_detector.start()

    yield
    await autodarts_detector.stop()
    _release_hardware()


def _release_hardware() -> None:
    led_controller.stop()


app = FastAPI(title="Autodarts", lifespan=lifespan)

# Dev convenience: the Vite dev server proxies /api, but allow direct calls too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(players_router)
app.include_router(games_router)
app.include_router(display_router)
app.include_router(update_router)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """Single event stream shared by every connected UI (main screen, any
    joined phones). Currently carries players.updated; detection/game
    events will use the same hub once they exist."""
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # clients don't send anything; just detects disconnect
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(websocket)


# ---------------------------------------------------------------- settings

@app.get("/api/settings")
def get_settings():
    return settings_store.load()


# ------------------------------------------------------------------ LEDs

class LedSettings(BaseModel):
    transport: str = Field(pattern="^(auto|serial|http|off)$")
    serial_port: str | None = None
    http_url: str = "http://led-controller.local"


@app.get("/api/leds/status")
def led_status():
    return led_controller.status()


@app.get("/api/leds/ports")
def led_ports():
    return {"ports": list_serial_ports()}


@app.get("/api/leds/effects")
def led_effects():
    return {"effects": [{"id": v, "name": k} for k, v in EFFECTS.items()]}


@app.get("/api/leds/info")
def led_info():
    try:
        return led_controller.request("info")
    except TransportError as e:
        raise HTTPException(503, str(e))


@app.post("/api/leds/state")
def led_state(state: dict):
    """Fire-and-forget partial state update; fx accepts names or ids."""
    led_controller.send(state)
    return {"queued": True}


@app.post("/api/leds/cue/{name}")
def led_cue(name: str):
    if not led_controller.cue(name):
        raise HTTPException(404, f"no cue named {name!r}")
    return {"queued": True}


@app.get("/api/leds/cues")
def led_cues():
    return {"cues": settings_store.load()["leds"]["cues"]}


@app.put("/api/settings/leds")
def put_led_settings(led: LedSettings):
    current = settings_store.load()["leds"]
    current.update(led.model_dump())
    settings = settings_store.update("leds", current)
    led_controller.reconfigure()
    return settings


# ------------------------------------------------------------- network

@app.get("/api/network/info")
def network_info():
    return {"hostname": socket.gethostname(), "ips": list_lan_ips()}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/detection/autodarts")
def autodarts_detector_status():
    """Is the Autodarts detection service reachable, connected and running?

    The UI uses this to tell the difference between "no darts because nobody's
    thrown" and "no darts because Autodarts isn't up" - and to offer a link to
    the Board Manager (http://<device-ip>:3180) for camera setup, which stays
    Autodarts' responsibility rather than something InterDarts reimplements.
    """
    return autodarts_detector.status()


@app.post("/api/detection/autodarts/reset")
async def autodarts_reset():
    """Reset Autodarts' board detection - the same "Manual reset" its own Board
    Manager fires. For a mis-detected visit: clear what the board currently sees
    so the darts can be thrown again, without leaving the play screen.
    """
    try:
        return await autodarts_detector.reset()
    except Exception as exc:
        raise HTTPException(503, f"could not reset the Autodarts board: {exc}")


@app.get("/api/detection/board-geometry")
def board_geometry():
    """Static dartboard geometry (radii + segment order) for drawing the
    front-on board diagram client-side. The frontend computes wedge shapes
    itself from this rather than duplicating board_model's constants, so there
    is exactly one source of truth for segment order and radii. Kept from the
    old detection router because the live play board (DartboardFace) needs it,
    independent of how darts are now detected.
    """
    return {
        "physical_board_radius_mm": board_model.PHYSICAL_BOARD_RADIUS_MM,
        "radii_mm": board_model.RADII_MM,
        "segments": board_model.SEGMENTS,
    }


# ------------------------------------------------------- built frontend
#
# Vue Router uses history mode (path-based, no #), so any path that isn't a
# real file under dist/ (e.g. /join, /calibration) must still resolve to
# index.html and let the client-side router take over. StaticFiles(html=True)
# alone only does this for "/", so we serve assets normally and fall back to
# index.html for everything else that isn't an /api/* route.

dist = paths.frontend_dist()
if dist.is_dir():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="frontend-assets")

    # index.html must never be served from cache. With no Cache-Control the
    # browser falls back to *heuristic* caching and can hand back a stale
    # copy without asking - which silently pins the app to an old build,
    # since index.html is what names the content-hashed asset bundles.
    # "no-cache" still allows a cheap 304 via ETag; it means "revalidate",
    # not "don't store". The hashed files under /assets are immutable by
    # construction (a new build produces a new filename), so they don't
    # need this and keep their default caching.
    NO_STORE_HTML = {"Cache-Control": "no-cache, must-revalidate"}

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html", headers=NO_STORE_HTML)
