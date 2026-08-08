"""Unit tests for the Autodarts state parser and visit lifecycle.

Covers the concrete requirements from codex-instructions.md: single/triple/bull
scoring, a full three-dart visit ending in a takeout, no duplicate darts from
repeated identical snapshots, and the startup guard against a stale
"Takeout finished".

Pure functions only - no network, no event loop, no httpx - so this runs
anywhere. Executable directly (``py -3.11 backend/tests/test_autodarts.py``) as
well as under pytest.
"""
from __future__ import annotations

import os
import sys
import types

# Allow `import detection.autodarts` / `games.base` when run from anywhere.
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _BACKEND)

# Import games.base WITHOUT running games/__init__.py, which pulls in fastapi
# (via the engine) that need not be installed just to test a pure parser. A
# stub 'games' package pre-seeded into sys.modules stops Python executing the
# real __init__; games/base.py itself is pure stdlib. Harmless under pytest in
# a full environment too - setdefault leaves an already-imported package alone.
if "games" not in sys.modules:
    _games = types.ModuleType("games")
    _games.__path__ = [os.path.join(_BACKEND, "games")]
    sys.modules["games"] = _games

from detection.autodarts import (  # noqa: E402
    DartDetected,
    TakeoutFinished,
    TakeoutStarted,
    VisitTracker,
    parse_throw,
)


def _throw(name, number, bed, multiplier, x=0.0, y=0.0):
    return {
        "segment": {"name": name, "number": number, "bed": bed, "multiplier": multiplier},
        "coords": {"x": x, "y": y},
    }


def _state(event, throws, num=None):
    return {"connected": True, "running": True, "status": "Throw",
            "event": event, "numThrows": num if num is not None else len(throws),
            "throws": throws}


# ----------------------------------------------------------------- parse_throw


def test_single():
    dart = parse_throw(_throw("S20", 20, "SingleOuter", 1, x=-0.087, y=0.762), 1)
    assert dart.segment == 20 and dart.multiplier == 1 and dart.score == 20
    assert dart.label == "S20"


def test_triple():
    dart = parse_throw(_throw("T3", 3, "Triple", 3), 1)
    assert dart.segment == 3 and dart.multiplier == 3 and dart.score == 9
    assert dart.label == "T3"


def test_double():
    dart = parse_throw(_throw("D16", 16, "Double", 2), 1)
    assert dart.score == 32 and dart.label == "D16"


def test_outer_bull():
    dart = parse_throw(_throw("25", 25, "Single", 1), 1)
    assert dart.segment == 25 and dart.multiplier == 1 and dart.score == 25
    assert dart.label == "25" and dart.is_outer_bull


def test_inner_bull():
    dart = parse_throw(_throw("BULL", 25, "Double", 2), 1)
    assert dart.score == 50 and dart.label == "BULL" and dart.is_bull


def test_miss_off_board():
    dart = parse_throw({"segment": {"name": "Out", "number": 0, "multiplier": 0}}, 1)
    assert dart.segment is None and dart.multiplier == 0 and dart.score == 0
    assert dart.label == "MISS" and dart.is_miss


def test_miss_outside_with_nearest_number():
    # A real Autodarts miss: nearest segment 6 but bed "Outside", multiplier 0.
    # The multiplier, not the number, marks the miss - segment must be None so
    # no game counts it as a hit on 6.
    dart = parse_throw(_throw("M6", 6, "Outside", 0, x=1.952, y=0.299), 1)
    assert dart.segment is None and dart.multiplier == 0 and dart.score == 0
    assert dart.label == "MISS" and dart.is_miss
    assert not dart.hits(6)


def test_coords_to_mm():
    # S20 at 12 o'clock: y+ up in Autodarts becomes y- (up) in board space.
    dart = parse_throw(_throw("S20", 20, "SingleOuter", 1, x=-0.087, y=0.762), 1)
    assert dart.x_mm is not None and abs(dart.x_mm - (-0.087 * 170)) < 1e-6
    assert dart.y_mm is not None and abs(dart.y_mm - (-0.762 * 170)) < 1e-6


# --------------------------------------------------------------- VisitTracker


def _darts(events):
    return [e for e in events if isinstance(e, DartDetected)]


def test_full_visit_then_takeout():
    """3 throws, a takeout, and a clear board -> exactly 3 darts, 1 started,
    1 finished."""
    t = VisitTracker()
    d1 = _throw("T20", 20, "Triple", 3)
    d2 = _throw("S5", 5, "SingleOuter", 1)
    d3 = _throw("D16", 16, "Double", 2)

    all_events = []
    all_events += t.update(_state("Throw detected", [d1]))
    all_events += t.update(_state("Throw detected", [d1, d2]))
    all_events += t.update(_state("Throw detected", [d1, d2, d3]))
    all_events += t.update(_state("Takeout started", [d1, d2, d3]))
    all_events += t.update(_state("Takeout finished", [], num=0))

    darts = _darts(all_events)
    assert [d.dart.label for d in darts] == ["T20", "S5", "D16"]
    assert [d.dart.score for d in darts] == [60, 5, 32]
    assert sum(isinstance(e, TakeoutStarted) for e in all_events) == 1
    assert sum(isinstance(e, TakeoutFinished) for e in all_events) == 1


def test_repeated_snapshots_no_duplicate_darts():
    """Polling identical states must not re-emit throws."""
    t = VisitTracker()
    d1 = _throw("T20", 20, "Triple", 3)
    events = []
    for _ in range(5):
        events += t.update(_state("Throw detected", [d1]))  # same snapshot 5x
    assert len(_darts(events)) == 1


def test_missed_poll_emits_gap():
    """If a poll is missed and the count jumps 0 -> 2, both darts are emitted
    from the throws array."""
    t = VisitTracker()
    d1 = _throw("T20", 20, "Triple", 3)
    d2 = _throw("S1", 1, "SingleOuter", 1)
    events = t.update(_state("Throw detected", [d1, d2]))  # jumped straight to 2
    assert [d.dart.label for d in _darts(events)] == ["T20", "S1"]


def test_startup_stale_takeout_ignored():
    """A first-ever snapshot already reading 'Takeout finished' must NOT fire a
    phantom visit-over."""
    t = VisitTracker()
    events = t.update(_state("Takeout finished", [], num=0))
    assert not any(isinstance(e, TakeoutFinished) for e in events)


def test_takeout_finished_not_repeated():
    """Holding on 'Takeout finished' across polls fires it once."""
    t = VisitTracker()
    d1 = _throw("T20", 20, "Triple", 3)
    t.update(_state("Throw detected", [d1]))
    events = []
    events += t.update(_state("Takeout finished", [], num=0))
    events += t.update(_state("Takeout finished", [], num=0))
    events += t.update(_state("Takeout finished", [], num=0))
    assert sum(isinstance(e, TakeoutFinished) for e in events) == 1


def test_takeout_without_started_still_finishes():
    """Autodarts can jump straight to 'Takeout finished' without a 'started'."""
    t = VisitTracker()
    d1 = _throw("T20", 20, "Triple", 3)
    t.update(_state("Throw detected", [d1]))
    events = t.update(_state("Takeout finished", [], num=0))
    assert sum(isinstance(e, TakeoutFinished) for e in events) == 1


def test_second_visit_after_reset():
    """After a takeout, the next visit's darts are emitted from a clean count."""
    t = VisitTracker()
    a = _throw("T20", 20, "Triple", 3)
    b = _throw("S5", 5, "SingleOuter", 1)
    t.update(_state("Throw detected", [a]))
    t.update(_state("Takeout finished", [], num=0))
    events = t.update(_state("Throw detected", [b]))
    darts = _darts(events)
    assert len(darts) == 1 and darts[0].dart.label == "S5"


# ------------------------------------------------------------------- runner


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
