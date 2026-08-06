"""Autodarts backend — FastAPI entry point.

Run from the backend/ directory:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import paths
import settings_store
from calibration.routes import router as calibration_router
from capture.devices import list_devices
from capture.manager import CaptureManager
from detection.pipeline import DetectionManager
from detection.routes import router as detection_router
from display import router as display_router
from events import hub
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

manager = CaptureManager()
detection_manager = DetectionManager(manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    paths.ensure_dirs()
    led_controller.start()
    # Also the state momentary cues (e.g. the dart-detected flash) fall back to.
    led_controller.set_resting_cue("startup")

    # Release the hardware before an update restart, which exits hard rather
    # than waiting on uvicorn's graceful shutdown (see update/restart.py).
    # Without this the new process would find the cameras and serial port
    # still held by the old one.
    update_restart.set_shutdown_hook(_release_hardware)
    update_routes.start_background_check()

    yield
    _release_hardware()


def _release_hardware() -> None:
    detection_manager.stop_all()
    manager.stop_all()
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
app.include_router(calibration_router)
app.include_router(detection_router)
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


# ---------------------------------------------------------------- cameras

@app.get("/api/cameras")
def get_cameras():
    return {"devices": list_devices()}


@app.get("/api/cameras/{device_id}/stream")
def stream_camera(device_id: int):
    """MJPEG live preview; camera is opened on demand and closed when the
    last viewer disconnects."""
    capture = settings_store.load()["capture"]
    stream = manager.acquire(
        device_id, capture["width"], capture["height"], capture["fps"]
    )

    def frames():
        last = 0
        try:
            while True:
                jpeg, last = stream.next_jpeg(last, timeout=2.0)
                if jpeg is None:
                    if stream.error:
                        break
                    continue
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
        finally:
            manager.release(device_id)

    return StreamingResponse(
        frames(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


# ---------------------------------------------------------------- settings

class CameraSlot(BaseModel):
    device_id: int
    name: str = ""


class CameraSelection(BaseModel):
    slots: list[CameraSlot | None] = Field(min_length=3, max_length=3)


@app.get("/api/settings")
def get_settings():
    return settings_store.load()


@app.put("/api/settings/cameras")
def put_camera_settings(selection: CameraSelection):
    chosen = [s.device_id for s in selection.slots if s is not None]
    if len(chosen) != len(set(chosen)):
        raise HTTPException(400, "the same device is assigned to more than one slot")
    settings = settings_store.update(
        "cameras", {"slots": [s.model_dump() if s else None for s in selection.slots]}
    )
    return settings


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
