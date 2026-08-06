"""Record and read back a takeout trace.

The detector's takeout decision is spread across four independent triggers in
session.py, and the one signal that actually separates "a dart arrived" from
"a dart left" - how far the board differs from the EMPTY-board reference - is
only ever computed inside _analyse_event. So nobody has seen its shape during
an actual removal. This captures it continuously, in every state, and prints
the result as a timeline you can read.

Usage:
    python tools/takeout_trace.py record --seconds 90 --out clips/takeout/run1.json
    python tools/takeout_trace.py show clips/takeout/run1.json
    python tools/takeout_trace.py show clips/takeout/run1.json --full
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _post(base_url: str, path: str) -> dict:
    request = urllib.request.Request(f"{base_url}{path}", method="POST", data=b"")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def _get(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=30) as response:
        return json.loads(response.read())


def record(base_url: str, seconds: float, out: Path) -> None:
    try:
        _post(base_url, "/api/detection/trace/start")
    except urllib.error.HTTPError as error:
        print(f"could not start tracing: {error.read().decode(errors='replace')}")
        raise SystemExit(1)
    print(f"tracing for {seconds:.0f}s - throw and remove darts normally now")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        print(f"\r  {remaining:5.1f}s left ", end="", flush=True)
        time.sleep(min(1.0, max(remaining, 0.05)))
    print()
    payload = _get(base_url, "/api/detection/trace")
    _post(base_url, "/api/detection/trace/stop")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1))
    samples = [s for s in payload.get("samples", []) if "mark" not in s]
    marks = [s for s in payload.get("samples", []) if "mark" in s]
    print(f"saved {len(samples)} samples and {len(marks)} marks -> {out}")


def show(path: Path, full: bool) -> None:
    payload = json.loads(path.read_text())
    rows = payload.get("samples", [])
    if not rows:
        print("empty trace")
        return
    constants = payload.get("constants", {})
    camera_ids = [str(c) for c in payload.get("camera_ids", [])]

    print(f"{len(rows)} entries, cameras {', '.join(camera_ids)}")
    print("thresholds: " + "  ".join(f"{k}={v}" for k, v in constants.items()))
    print()
    header = f"{'t':>7} {'state':>16} {'aw':>3} {'occ %':>7} {'stored %':>9} " \
             f"{'vs baseline (per camera) %':<30} {'moving %':<22}"
    print(header)
    print("-" * len(header))

    previous_state = None
    for row in rows:
        if "mark" in row:
            extra = {k: v for k, v in row.items() if k not in {"t", "mark", "state"}}
            print(f"{row['t']:>7.1f} {'** ' + row['mark']:>16} "
                  + "  ".join(f"{k}={v}" for k, v in extra.items()))
            continue
        state_changed = row["state"] != previous_state
        previous_state = row["state"]
        # Quiet rows are the bulk of any trace and say nothing; keep them only
        # on --full, but never drop a row where the state just changed.
        interesting = (
            state_changed
            or row.get("suppressed")
            or (row.get("occupancy") or 0) > constants.get("EMPTY_BOARD_OCCUPANCY", 0.002)
            or any((c.get("baseline") or 0) > constants.get("MOTION_TRIGGER_RATIO", 0.00085)
                   for c in row["cameras"].values())
        )
        if not (full or interesting):
            continue
        occupancy = "-" if row.get("occupancy") is None else f"{row['occupancy'] * 100:7.3f}"
        stored = f"{row['stored_occupancy'] * 100:9.3f}"
        baselines = "  ".join(
            f"{cid}:{(row['cameras'].get(cid, {}).get('baseline') or 0) * 100:6.2f}" for cid in camera_ids
        )
        moving = "  ".join(
            f"{cid}:{(row['cameras'].get(cid, {}).get('previous') or 0) * 100:5.2f}" for cid in camera_ids
        )
        flag = "!" if row.get("suppressed") else " "
        print(f"{row['t']:>7.1f} {row['state']:>16}{flag}{'Y' if row.get('awaiting') else '.':>2} "
              f"{occupancy} {stored} {baselines:<30} {moving:<22}")

    print()
    marks = [r for r in rows if "mark" in r]
    takeouts = [m for m in marks if m["mark"] == "takeout"]
    hits = [m for m in marks if m["mark"] == "hit"]
    tests = [m for m in marks if m["mark"] == "occupancy_test"]
    print(f"summary: {len(hits)} analysed events, {len(takeouts)} takeouts, {len(tests)} occupancy tests")
    for mark in takeouts:
        print(f"  takeout at {mark['t']:.1f}s from {mark['state']}: {mark['reason']} "
              f"(awaiting={mark.get('awaiting')}, stored occupancy={mark.get('stored_occupancy', 0) * 100:.2f}%)")
    for mark in tests:
        measured = mark.get("measured")
        print(f"  occupancy test at {mark['t']:.1f}s: measured "
              f"{'-' if measured is None else f'{measured * 100:.2f}%'} vs stored "
              f"{mark.get('stored', 0) * 100:.2f}% -> {'TAKEOUT' if mark.get('would_fire') else 'score it'}")


def selfcheck() -> int:
    """Reproduce the double-takeout directly, without a board or cameras.

    The live symptom was a genuine takeout immediately followed by a phantom
    one, which advanced the turn twice and skipped a player. It came from
    `_occupancy` describing the board as it was BEFORE the removal, because
    only _analyse_event ever updated it and three of the four takeout paths
    return before reaching it.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import numpy as np

    from detection import session as session_module

    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"  {'PASS' if condition else 'FAIL'}  {name}{'  - ' + detail if detail else ''}")
        if not condition:
            failures.append(name)

    empty = np.zeros((96, 96, 3), dtype=np.uint8)
    darts = empty.copy()
    darts[:17, :17] = 255  # ~3% of the frame, three darts' worth

    detector = session_module.DetectionSession([1, 2], object(), None)
    detector._roi = lambda camera_id, frame: np.full(frame.shape[:2], 255, dtype=np.uint8)

    def learn(frame):
        detector._reset_to_baseline()
        for _ in range(6):
            detector._learn_baseline({1: frame.copy(), 2: frame.copy()})

    # Start of session: empty board becomes both baseline and reference.
    learn(empty)
    check("empty board reads as unoccupied", detector._occupancy <= session_module.EMPTY_BOARD_OCCUPANCY,
          f"{detector._occupancy * 100:.2f}%")

    # Three darts land. _analyse_event would record this occupancy.
    detector._note_occupancy(detector._board_occupancy({1: darts, 2: darts}))
    occupied = detector._occupancy
    check("three darts read as occupied", occupied > 0.02, f"{occupied * 100:.2f}%")

    # The darts come out. A takeout fires via a path that returns before
    # _note_occupancy, then clearing relearns the baseline on the empty board.
    learn(empty)
    check("stored occupancy follows the board back down after a takeout",
          detector._occupancy <= session_module.EMPTY_BOARD_OCCUPANCY,
          f"was {occupied * 100:.2f}%, now {detector._occupancy * 100:.2f}%")

    # The next event analysed on that empty board must NOT look like a takeout.
    measured = detector._board_occupancy({1: empty, 2: empty})
    would_fire = measured < detector._occupancy - session_module.TAKEOUT_OCCUPANCY_DROP
    check("no phantom second takeout on the now-empty board", not would_fire,
          f"measured {measured * 100:.2f}% vs stored {detector._occupancy * 100:.2f}%")

    # ...and a real removal must still be detectable, or the fix would have
    # simply disabled the occupancy trigger.
    detector._note_occupancy(detector._board_occupancy({1: darts, 2: darts}))
    one_left = empty.copy()
    one_left[:10, :10] = 255
    measured = detector._board_occupancy({1: one_left, 2: one_left})
    check("a genuine drop still trips the takeout test",
          measured < detector._occupancy - session_module.TAKEOUT_OCCUPANCY_DROP,
          f"measured {measured * 100:.2f}% vs stored {detector._occupancy * 100:.2f}%")

    # --- clearing must wait for an EMPTY board, not merely a still one -----
    #
    # The sequence a real game produced: a takeout fires, the player pauses
    # mid-removal with a dart still in the board, and the board goes still.
    # Stillness alone used to end clearing there.
    print()
    advanced = []
    detector = session_module.DetectionSession([1, 2], object(), None)
    detector._roi = lambda camera_id, frame: np.full(frame.shape[:2], 255, dtype=np.uint8)
    detector._notify_game_takeout = lambda: advanced.append(True)
    detector._game_awaiting_takeout = lambda: True
    learn(empty)   # reference = the empty board

    def hold_still(frame, seconds=2.0):
        """Feed identical frames until the stillness timer would have expired."""
        steps = int(seconds / 0.05) + 1
        for _ in range(steps):
            detector._clear_stable_since = detector._clear_stable_since or (
                time.monotonic() - session_module.CLEARING_STABLE_SECONDS - 0.1
            )
            detector._clearing({1: frame.copy(), 2: frame.copy()})
            if detector.state != "clearing":
                return
            # Re-arm so the next pass is treated as "still for long enough".
            detector._clear_stable_since = time.monotonic() - session_module.CLEARING_STABLE_SECONDS - 0.1

    detector._begin_clearing()
    hold_still(one_left)      # hand gone, but a dart is still in the board
    check("clearing holds while a dart is still in the board",
          detector.state == "clearing" and not advanced,
          f"state={detector.state}, turn advanced={bool(advanced)}")
    check("and says so, rather than stalling silently",
          "still in the board" in detector.message.lower(), detector.message)

    hold_still(empty)         # the last dart finally comes out
    check("clearing completes once the board is actually empty",
          detector.state == "learning_baseline", f"state={detector.state}")
    check("the turn advances exactly once, at that point", len(advanced) == 1,
          f"advanced {len(advanced)} time(s)")

    # --- and it must not advance twice if the human got there first --------
    advanced.clear()
    detector._game_awaiting_takeout = lambda: True
    detector._begin_clearing()
    detector._game_awaiting_takeout = lambda: False   # human pressed "Darts removed"
    hold_still(empty)
    check("no second advance when the takeout was confirmed by hand",
          not advanced, f"advanced {len(advanced)} time(s)")

    # --- mid-turn, holding out for an empty board would be worse -----------
    #
    # A hand reaching past the board while the player still has darts to throw
    # fires the same triggers. Waiting for "empty" there would block the rest of
    # their turn, so stillness alone resumes - but the turn must NOT advance,
    # because nothing actually came out.
    print()
    advanced.clear()
    detector._game_awaiting_takeout = lambda: False   # turn is not full
    detector._begin_clearing()
    hold_still(one_left)
    check("mid-turn, play resumes rather than waiting for an empty board",
          detector.state == "learning_baseline", f"state={detector.state}")
    check("and a hand reaching past the board costs nobody a turn",
          not advanced, f"advanced {len(advanced)} time(s)")

    advanced.clear()
    detector._begin_clearing()
    hold_still(empty)
    check("mid-turn, genuinely clearing the board does advance the turn",
          len(advanced) == 1, f"advanced {len(advanced)} time(s)")

    print(f"\n{'all checks passed' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selfcheck", help="reproduce the double-takeout bug and prove it is fixed")

    recorder = sub.add_parser("record", help="capture a trace from a running detector")
    recorder.add_argument("--seconds", type=float, default=90.0)
    recorder.add_argument("--out", type=Path, default=Path("clips/takeout/trace.json"))
    recorder.add_argument("--url", default="http://localhost:8000")

    reader = sub.add_parser("show", help="print a saved trace as a timeline")
    reader.add_argument("path", type=Path)
    reader.add_argument("--full", action="store_true", help="include quiet rows")

    args = parser.parse_args()
    if args.command == "record":
        record(args.url, args.seconds, args.out)
    elif args.command == "selfcheck":
        raise SystemExit(selfcheck())
    else:
        show(args.path, args.full)


if __name__ == "__main__":
    sys.exit(main())
