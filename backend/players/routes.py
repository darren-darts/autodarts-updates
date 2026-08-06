from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from events import hub
from players import store

router = APIRouter(prefix="/api/players", tags=["players"])

MAX_SELFIE_BYTES = 6 * 1024 * 1024


class PlayerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=24)
    avatar: str | None = None


async def _broadcast(players: list[dict]) -> None:
    await hub.broadcast({"type": "players.updated", "players": players})


@router.get("")
def get_players():
    return {
        "players": store.list_players(),
        "min_players": store.MIN_PLAYERS,
        "max_players": store.MAX_PLAYERS,
    }


@router.post("")
async def add_player():
    try:
        players = store.add_player()
    except ValueError as e:
        raise HTTPException(400, str(e))
    await _broadcast(players)
    return {"players": players}


@router.patch("/{player_id}")
async def patch_player(player_id: str, body: PlayerUpdate):
    try:
        players = store.update_player(player_id, name=body.name, avatar=body.avatar)
    except KeyError:
        raise HTTPException(404, "no such player")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await _broadcast(players)
    return {"players": players}


@router.delete("/{player_id}")
async def delete_player(player_id: str):
    try:
        players = store.remove_player(player_id)
    except KeyError:
        raise HTTPException(404, "no such player")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await _broadcast(players)
    return {"players": players}


@router.post("/{player_id}/selfie")
async def upload_selfie(player_id: str, file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_SELFIE_BYTES:
        raise HTTPException(400, "selfie too large")
    try:
        players = store.set_selfie(player_id, data)
    except KeyError:
        raise HTTPException(404, "no such player")
    await _broadcast(players)
    return {"players": players}


@router.delete("/{player_id}/selfie")
async def delete_selfie(player_id: str):
    try:
        players = store.clear_selfie(player_id)
    except KeyError:
        raise HTTPException(404, "no such player")
    await _broadcast(players)
    return {"players": players}


@router.get("/{player_id}/selfie.jpg")
def get_selfie(player_id: str):
    path = store.selfie_path(player_id)
    if not path.exists():
        raise HTTPException(404, "no selfie")
    return FileResponse(path, media_type="image/jpeg")
