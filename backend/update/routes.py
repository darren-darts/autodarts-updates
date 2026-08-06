"""/api/update/* - what the Updates page talks to.

Every endpoint returns the same status shape, so the UI has exactly one
thing to render and never has to reconcile two representations of what is
going on. Long operations (check, download) run on background threads and
publish progress over the existing WebSocket hub, so the page shows a live
byte count without polling.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import paths
import settings_store
from events import hub

from . import client, restart, version as version_mod

log = logging.getLogger("autodarts.update")

router = APIRouter(prefix="/api/update", tags=["update"])

_loop: asyncio.AbstractEventLoop | None = None


def _broadcast() -> None:
    """Push status to every connected UI.

    Called from the updater's own threads, so it hops onto the event loop
    the same way the detection pipeline does - this is the established
    thread-to-asyncio bridge in this codebase, not a new mechanism.
    """
    if _loop is None or _loop.is_closed():
        return
    payload = {"type": "update.status", **client.updater.status()}
    try:
        asyncio.run_coroutine_threadsafe(hub.broadcast(payload), _loop)
    except RuntimeError:
        pass


client.updater.set_listener(_broadcast)


def _capture_loop() -> None:
    global _loop
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None


def last_update_result() -> dict | None:
    """What the launcher recorded about the previous start, if anything.

    This is how the UI can say "updated to 1.2.0" or, more importantly,
    "1.2.0 would not start so 1.1.0 was restored" - the app itself was not
    running when either of those happened, so the launcher's breadcrumb is
    the only account of it.
    """
    try:
        raw = (paths.install_root() / "last_update.json").read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@router.get("/status")
async def get_status() -> dict:
    _capture_loop()
    return {**client.updater.status(), "last_result": last_update_result()}


@router.post("/check")
async def check() -> dict:
    _capture_loop()
    status = await asyncio.to_thread(client.updater.check)
    return {**status, "last_result": last_update_result()}


@router.post("/download")
async def download() -> dict:
    _capture_loop()
    return client.updater.start_download()


@router.post("/cancel")
async def cancel() -> dict:
    return client.updater.cancel()


@router.post("/discard")
async def discard() -> dict:
    return client.updater.clear_pending()


@router.post("/restart")
async def restart_now() -> dict:
    """Exit so the launcher can swap in the staged update and start again.

    Refused unless something is actually staged: without a launcher watching
    the exit code, this would just close the app, and doing that on a whim
    to someone mid-game is not acceptable.
    """
    pending = client.read_pending()
    if not pending:
        raise HTTPException(400, "There is no downloaded update waiting to be installed.")
    restart.request_restart()
    return {
        "restarting": True,
        "version": pending.get("version"),
        "message": "Installing the update and restarting. This page will reconnect shortly.",
    }


class UpdateSettings(BaseModel):
    channel: str | None = Field(default=None)
    auto_check: bool | None = None
    base_url: str | None = None


@router.post("/settings")
async def set_settings(payload: UpdateSettings) -> dict:
    settings = settings_store.load()
    section = dict(settings.get("updates", {}))

    if payload.channel is not None:
        if payload.channel not in version_mod.CHANNELS:
            raise HTTPException(400, f"channel must be one of {list(version_mod.CHANNELS)}")
        section["channel"] = payload.channel
    if payload.auto_check is not None:
        section["auto_check"] = bool(payload.auto_check)
    if payload.base_url is not None:
        section["base_url"] = payload.base_url.strip()

    settings_store.update("updates", section)
    return {**client.updater.status(), "settings": section}


@router.get("/settings")
async def get_settings() -> dict:
    return settings_store.load().get("updates", {})


def start_background_check() -> None:
    """Check once, shortly after startup, if the user has left that enabled.

    Delayed rather than immediate: at startup the cameras are being opened
    and the LED controller is connecting, and a network call that blocks on
    a slow DNS lookup is not worth adding to that. Failures are swallowed -
    an update check is never a reason for the app not to work.
    """
    if not settings_store.load().get("updates", {}).get("auto_check", True):
        return
    if not client.can_update():
        return

    def worker() -> None:
        time.sleep(8)
        try:
            client.updater.check()
        except Exception:  # noqa: BLE001
            log.exception("startup update check failed")

    threading.Thread(target=worker, name="update-startup-check", daemon=True).start()
