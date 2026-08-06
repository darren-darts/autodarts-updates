"""Effect ids exposed by the ESP32 firmware.

Must stay in sync with EffectMode in led-controller/include/effects.h.
Cue definitions may reference effects by these names instead of raw ids.
"""

EFFECTS = {
    "SOLID": 0,
    "RAINBOW": 1,
    "RAINBOW_CYCLE": 2,
    "THEATER_CHASE": 3,
    "THEATER_CHASE_RAINBOW": 4,
    "COLOR_WIPE": 5,
    "COLOR_WIPE_RANDOM": 6,
    "SCAN": 7,
    "DUAL_SCAN": 8,
    "TWINKLE": 9,
    "SPARKLE": 10,
    "BREATHE": 11,
    "STROBE": 12,
    "FIRE": 13,
    "COMET": 14,
    "METEOR": 15,
    "RUNNING_LIGHTS": 16,
    "CONFETTI": 17,
    "JUGGLE": 18,
    "BPM": 19,
    "FADE": 20,
    "CHASE_RAINBOW": 21,
    "RED": 22,
    "GREEN": 23,
    "BLUE": 24,
    "WHITE": 25,
    "YELLOW": 26,
    "ORANGE": 27,
    "PURPLE": 28,
    "CYAN": 29,
    "PINK": 30,
    "FLASH_3": 31,
    "PULSE": 32,
    "HEARTBEAT": 33,
    "COUNTDOWN": 34,
    "SPINNER": 35,
    "CHECKER": 36,
    "POLICE": 37,
    "BULLSEYE": 38,
    "CELEBRATION": 39,
    "WAVE": 40,
}

EFFECT_NAMES = {v: k for k, v in EFFECTS.items()}


def resolve_fx(value) -> int:
    """Accept an effect id or name; return the id."""
    if isinstance(value, int):
        return value
    key = str(value).strip().upper().replace(" ", "_")
    if key not in EFFECTS:
        raise ValueError(f"unknown effect {value!r}")
    return EFFECTS[key]
