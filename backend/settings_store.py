"""Persistent app settings in config/settings.json at the project root."""
from __future__ import annotations

import copy
import json
import threading

from paths import CONFIG_DIR

SETTINGS_PATH = CONFIG_DIR / "settings.json"

DEFAULTS: dict = {
    # Three camera slots; each is null or {"device_id": int, "name": str}
    "cameras": {"slots": [None, None, None]},
    "capture": {"width": 1280, "height": 720, "fps": 30},
    "leds": {
        # transport: auto (prefer USB serial, fall back to WiFi), serial, http, off
        "transport": "auto",
        "serial_port": None,  # e.g. "COM5" or "/dev/ttyUSB0"; null = auto-detect
        "http_url": "http://led-controller.local",
        # Named cues used throughout the app; fx accepts effect names or ids.
        # State schema matches the firmware: on/bri/fx/sx/col/col2.
        "cues": {
            # Plain white at full brightness is the app's resting state: it
            # lights the board for the cameras, so it doubles as task
            # lighting rather than just decoration. Transient cues (see
            # led_controller.flash_cue) return here when they finish.
            # Every cue below is fired as a *momentary* flash that returns to
            # "startup". Bright flat white is the playing state, because it's
            # what the cameras are calibrated under and what they score
            # reliably in; colour is for moments, never the background.
            # All run at full brightness so they read across the room.
            "startup": {"on": True, "bri": 255, "fx": "SOLID", "col": [255, 255, 255]},
            "idle": {"fx": "PULSE", "col": [0, 40, 120], "sx": 40},
            "calibration.start": {"fx": "SPINNER", "col": [0, 120, 255], "sx": 30},
            "calibration.point": {"fx": "FLASH_3", "col": [0, 255, 80]},
            "calibration.done": {"fx": "CELEBRATION", "sx": 20},
            "game.start": {"on": True, "bri": 255, "fx": "WAVE", "col": [0, 220, 90], "col2": [0, 60, 200], "sx": 25},
            "throw.detected": {"on": True, "bri": 255, "fx": "SOLID", "col": [0, 255, 0]},
            # Darts coming out of the board. Red so it reads as the opposite of
            # the green "that scored" flash from across the room, and paired
            # with its own sound on the play screen.
            "takeout": {"on": True, "bri": 255, "fx": "SOLID", "col": [255, 0, 0]},
            "turn.start": {"on": True, "bri": 255, "fx": "COMET", "col": [0, 160, 255], "sx": 25},
            "bust": {"on": True, "bri": 255, "fx": "POLICE", "sx": 30},
            "bullseye": {"on": True, "bri": 255, "fx": "BULLSEYE", "sx": 45},
            "score.180": {"on": True, "bri": 255, "fx": "CELEBRATION", "sx": 12},
            "game.win": {"on": True, "bri": 255, "fx": "CELEBRATION", "sx": 18},
        },
    },
    "updates": {
        # "stable" or "beta". Beta exists so a release can be tried on one
        # machine before the rest of the family is offered it.
        "channel": "stable",
        # Check on startup. Downloading and installing always stay manual -
        # the app runs unattended next to a dartboard, and a self-restart
        # mid-match would be far more annoying than an out-of-date version.
        "auto_check": True,
        # Overrides the origin compiled into the build. Empty in normal use;
        # exists so the whole update path can be exercised against a local
        # server without publishing anything.
        "base_url": "",
    },
}

_lock = threading.Lock()


def _merge_defaults(defaults: dict, loaded: dict) -> dict:
    merged = copy.deepcopy(defaults)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(merged[key], value)
        else:
            merged[key] = value
    return merged


def load() -> dict:
    with _lock:
        if SETTINGS_PATH.exists():
            try:
                loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                return _merge_defaults(DEFAULTS, loaded)
            except (json.JSONDecodeError, OSError):
                pass
        return copy.deepcopy(DEFAULTS)


def save(settings: dict) -> None:
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)


def update(section: str, value) -> dict:
    settings = load()
    settings[section] = value
    save(settings)
    return settings
