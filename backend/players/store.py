"""Shared player roster - config/players.json.

Single source of truth for both the main screen and any phone that has
joined; both interfaces read/write the same roster via the API in
players/routes.py, which broadcasts changes over the WebSocket hub so every
connected UI stays in sync without polling.
"""
from __future__ import annotations

import json
import secrets
import threading

from paths import CONFIG_DIR

PLAYERS_PATH = CONFIG_DIR / "players.json"
SELFIES_DIR = CONFIG_DIR / "selfies"

MIN_PLAYERS = 1
MAX_PLAYERS = 8  # sane cap for one board's turn order

# Kept in sync with tools/generate_avatars.py's PALETTE length (12).
DEFAULT_AVATARS = [f"/avatars/avatar-{i:02d}.svg" for i in range(1, 13)]

_lock = threading.Lock()


def _default_players() -> list[dict]:
    return [
        {"id": secrets.token_hex(4), "name": "Player 1", "avatar": DEFAULT_AVATARS[0]},
        {"id": secrets.token_hex(4), "name": "Player 2", "avatar": DEFAULT_AVATARS[1]},
    ]


def _load_raw() -> list[dict]:
    if PLAYERS_PATH.exists():
        try:
            return json.loads(PLAYERS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    players = _default_players()
    _save_raw(players)
    return players


def _save_raw(players: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PLAYERS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(players, indent=2), encoding="utf-8")
    tmp.replace(PLAYERS_PATH)


def _next_default_avatar(existing: list[dict]) -> str:
    used = {p["avatar"] for p in existing}
    for avatar in DEFAULT_AVATARS:
        if avatar not in used:
            return avatar
    return DEFAULT_AVATARS[len(existing) % len(DEFAULT_AVATARS)]


def list_players() -> list[dict]:
    with _lock:
        return _load_raw()


def add_player() -> list[dict]:
    with _lock:
        players = _load_raw()
        if len(players) >= MAX_PLAYERS:
            raise ValueError(f"maximum {MAX_PLAYERS} players")
        existing_names = {p["name"] for p in players}
        n = len(players) + 1
        name = f"Player {n}"
        while name in existing_names:
            n += 1
            name = f"Player {n}"
        players.append(
            {"id": secrets.token_hex(4), "name": name, "avatar": _next_default_avatar(players)}
        )
        _save_raw(players)
        return players


def remove_player(player_id: str) -> list[dict]:
    with _lock:
        players = _load_raw()
        if len(players) <= MIN_PLAYERS:
            raise ValueError(f"at least {MIN_PLAYERS} player is required")
        remaining = [p for p in players if p["id"] != player_id]
        if len(remaining) == len(players):
            raise KeyError(player_id)
        _save_raw(remaining)
        selfie_path(player_id).unlink(missing_ok=True)
        return remaining


def update_player(player_id: str, *, name: str | None = None, avatar: str | None = None) -> list[dict]:
    with _lock:
        players = _load_raw()
        for p in players:
            if p["id"] != player_id:
                continue
            if name is not None:
                name = name.strip()
                if not name:
                    raise ValueError("name cannot be empty")
                p["name"] = name[:24]
            if avatar is not None:
                p["avatar"] = avatar
            _save_raw(players)
            return players
        raise KeyError(player_id)


def selfie_path(player_id: str) -> Path:
    return SELFIES_DIR / f"{player_id}.jpg"


def set_selfie(player_id: str, data: bytes) -> list[dict]:
    with _lock:
        players = _load_raw()
        if not any(p["id"] == player_id for p in players):
            raise KeyError(player_id)
        SELFIES_DIR.mkdir(parents=True, exist_ok=True)
        selfie_path(player_id).write_bytes(data)
        for p in players:
            if p["id"] == player_id:
                # cache-busting query so <img> tags refresh after a retake
                p["avatar"] = f"/api/players/{player_id}/selfie.jpg?v={secrets.token_hex(3)}"
        _save_raw(players)
        return players


def clear_selfie(player_id: str) -> list[dict]:
    with _lock:
        players = _load_raw()
        if not any(p["id"] == player_id for p in players):
            raise KeyError(player_id)
        selfie_path(player_id).unlink(missing_ok=True)
        for p in players:
            if p["id"] == player_id:
                p["avatar"] = _next_default_avatar([o for o in players if o["id"] != player_id])
        _save_raw(players)
        return players
