"""Offline replay benchmark for the detection pipeline.

Runs the *real* axis + fusion code against saved evidence frames, so an
algorithm change can be scored before it ever meets a dartboard. This is the
regression half of the "dart lab" in PLAN.md: every corrected throw the user
makes enlarges the labelled set, and every tuning decision has to justify
itself here rather than against one memorable failure.

A clip library lives in `clips/bench/`:
    status.json   - a snapshot of GET /api/detection/status (the event history)
    frames/       - evidence JPEGs, ev{event_id}_cam{camera_id}.jpg
    labels.json   - ground truth, see that file's own _comment

Capture a fresh one straight off a running server with --capture.

Usage:
    python tools/detection_bench.py                       # score the library
    python tools/detection_bench.py --capture             # pull a new library
    python tools/detection_bench.py --json out.json       # machine-readable
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from calibration import store as calibration_store  # noqa: E402
from detection import axis as axis_module  # noqa: E402
from detection.fusion import fuse_axes  # noqa: E402
from detection.scoring import score_board_point  # noqa: E402

DEFAULT_LIBRARY = ROOT / "clips" / "bench"

# A single dart changes a small, characteristic slice of the board. Replay
# assumes the previous event's frame is this event's "before" picture, which
# is only true if nothing else happened in between - a hand reaching in
# between two events was once enough to inflate mean line error from 3.85mm
# to 23.59mm on its own. Pairs outside this band are reported and excluded
# rather than quietly averaged in.
MIN_PLAUSIBLE_CHANGE = 0.0005
MAX_PLAUSIBLE_CHANGE = 0.05
# How closely a replay has to land on the result the live detector actually
# produced before it counts as the same event. Bigger than this and the
# chosen "before" frame was probably wrong, so the case says nothing about
# the algorithm.
REPRODUCTION_TOLERANCE_MM = 6.0


def capture(base_url: str, library: Path, max_events: int = 60) -> None:
    library.mkdir(parents=True, exist_ok=True)
    (library / "frames").mkdir(exist_ok=True)
    with urllib.request.urlopen(f"{base_url}/api/detection/status", timeout=10) as response:
        status = json.loads(response.read())
    (library / "status.json").write_text(json.dumps(status, indent=1))
    saved = 0
    for event in status.get("history", []):
        event_id = event["event_id"]
        for camera_id in event.get("evidence_cameras") or []:
            url = f"{base_url}/api/detection/history/{event_id}/frame/{camera_id}"
            try:
                with urllib.request.urlopen(url, timeout=10) as response:
                    (library / "frames" / f"ev{event_id}_cam{camera_id}.jpg").write_bytes(response.read())
                saved += 1
            except urllib.error.HTTPError:
                pass  # evidence for that event/camera has aged out of the ring buffer
    print(f"captured {len(status.get('history', []))} events, {saved} frames -> {library}")


def _frames_for(library: Path, event_id: int) -> dict[int, np.ndarray]:
    out: dict[int, np.ndarray] = {}
    for path in sorted((library / "frames").glob(f"ev{event_id}_cam*.jpg")):
        camera_id = int(path.stem.split("_cam")[1])
        image = cv2.imread(str(path))
        if image is not None:
            out[camera_id] = image
    return out


def replay_event(library: Path, event: dict, previous_id: int | None) -> dict | None:
    """Re-run axis detection + fusion for one stored event. Returns None when
    the event has no usable before/after frame pair."""
    if previous_id is None:
        return None
    post = _frames_for(library, event["event_id"])
    pre = _frames_for(library, previous_id)
    shared = sorted(set(pre) & set(post))
    if len(shared) < 2:
        return None

    calibrations = calibration_store.get_all()
    candidates = []
    per_camera: dict[int, dict] = {}
    for camera_id in shared:
        profile = calibrations.get(camera_id) or calibrations.get(str(camera_id))
        if not profile:
            continue
        if pre[camera_id].shape != post[camera_id].shape:
            continue
        homography = np.array(profile["homography"], dtype=np.float64)
        analysis = axis_module.detect_dart_axis(
            camera_id, pre[camera_id], post[camera_id], homography, np.linalg.inv(homography)
        )
        per_camera[camera_id] = {
            "changed_ratio": analysis.changed_ratio,
            "reason": analysis.reason,
            "confidence": analysis.candidate.confidence if analysis.candidate else 0.0,
            "board_line": list(analysis.candidate.board_line) if analysis.candidate else None,
        }
        if analysis.candidate:
            candidates.append(analysis.candidate)

    ratios = [info["changed_ratio"] for info in per_camera.values()]
    pair_valid = bool(ratios) and max(ratios) <= MAX_PLAUSIBLE_CHANGE and max(ratios) >= MIN_PLAUSIBLE_CHANGE
    if len(candidates) < 2:
        return {
            "event_id": event["event_id"], "pair_valid": pair_valid, "per_camera": per_camera,
            "hit": None, "reproduced": False,
        }

    hit = fuse_axes(candidates)
    reproduced = False
    if hit.x_mm is not None and event.get("x_mm") is not None:
        reproduced = math.dist((hit.x_mm, hit.y_mm), (event["x_mm"], event["y_mm"])) <= REPRODUCTION_TOLERANCE_MM
    elif hit.x_mm is None and event.get("x_mm") is None:
        reproduced = True
    return {
        "event_id": event["event_id"], "pair_valid": pair_valid, "per_camera": per_camera,
        "hit": hit, "reproduced": reproduced,
    }


def _leave_one_out(per_camera: dict[int, dict]) -> dict[int, float]:
    """Each camera's board line vs the crossing of the other two - the
    measurement that says whether an outlier is even identifiable."""
    lines = {c: info["board_line"] for c, info in per_camera.items() if info["board_line"]}
    if len(lines) != 3:
        return {}
    out = {}
    for camera_id, (a, b, c) in lines.items():
        others = [lines[k] for k in lines if k != camera_id]
        (a1, b1, c1), (a2, b2, c2) = others
        determinant = a1 * b2 - a2 * b1
        if abs(determinant) < 1e-9:
            continue
        x = (b1 * c2 - b2 * c1) / determinant
        y = (c1 * a2 - c2 * a1) / determinant
        out[camera_id] = abs(a * x + b * y + c)
    return out


def run(library: Path) -> dict:
    status = json.loads((library / "status.json").read_text())
    labels = json.loads((library / "labels.json").read_text()) if (library / "labels.json").exists() else {}
    history = sorted(status["history"], key=lambda e: e["event_id"])

    rows = []
    previous_id = None
    for event in history:
        result = replay_event(library, event, previous_id)
        previous_id = event["event_id"]
        if result is None:
            continue
        truth = labels.get(str(event["event_id"]))
        row = {
            "event_id": event["event_id"],
            "stored_label": event["label"],
            "pair_valid": result["pair_valid"],
            "reproduced": result["reproduced"],
            "hit": result["hit"],
            "leave_one_out": _leave_one_out(result["per_camera"]),
            "truth": truth,
            "label": None, "position_error_mm": None, "segment_ok": None,
        }
        hit = result["hit"]
        if hit is not None:
            row["label"] = hit.label
            if truth and hit.x_mm is not None:
                if truth.get("x_mm") is not None:
                    row["position_error_mm"] = math.dist((hit.x_mm, hit.y_mm), (truth["x_mm"], truth["y_mm"]))
                scored = score_board_point(hit.x_mm, hit.y_mm)
                row["segment_ok"] = (
                    scored["segment"] == truth["segment"] and scored["ring"] == truth["ring"]
                )
        rows.append(row)
    return {"rows": rows}


def report(result: dict) -> None:
    rows = result["rows"]
    usable = [r for r in rows if r["pair_valid"] and r["reproduced"]]
    print(f"{'ev':>4} {'stored':>7} {'replay':>7} {'truth':>7} {'ok':>4} {'err mm':>7}  {'leave-one-out (mm)':<34} notes")
    print("-" * 118)
    for row in rows:
        truth = row["truth"] or {}
        truth_label = ""
        if truth:
            ring = truth.get("ring")
            prefix = {"triple": "T", "double": "D", "single": "S"}.get(ring, "")
            truth_label = "BULL" if ring == "bullseye" else "25" if ring == "outer_bull" else f"{prefix}{truth['segment']}"
        ok = "" if row["segment_ok"] is None else ("yes" if row["segment_ok"] else "NO")
        err = "" if row["position_error_mm"] is None else f"{row['position_error_mm']:.2f}"
        loo = "  ".join(f"c{c}:{v:6.2f}" for c, v in sorted(row["leave_one_out"].items()))
        notes = []
        if not row["pair_valid"]:
            notes.append("before/after pair invalid")
        if not row["reproduced"]:
            notes.append("did not reproduce stored result")
        print(f"{row['event_id']:>4} {row['stored_label']:>7} {str(row['label'] or '-'):>7} {truth_label:>7} "
              f"{ok:>4} {err:>7}  {loo:<34} {'; '.join(notes)}")

    scored = [r for r in usable if r["segment_ok"] is not None]
    correct = [r for r in scored if r["segment_ok"]]
    errors = [r["position_error_mm"] for r in usable if r["position_error_mm"] is not None]
    print("-" * 118)
    print(f"usable events (valid pair + reproduced): {len(usable)} of {len(rows)}")
    print(f"segment/ring correct: {len(correct)}/{len(scored)}")
    if errors:
        print(f"position error vs measured truth: mean {np.mean(errors):.2f} mm, max {np.max(errors):.2f} mm  (n={len(errors)})")


def _synthetic_candidate(camera_id: int, board_line, confidence: float):
    """A candidate carrying an exact board-space line, for geometry checks that
    should not depend on any image processing."""
    from detection.models import AxisCandidate

    return AxisCandidate(
        camera_id=camera_id, image_line=(0.0, 0.0, 100.0, 100.0), board_line=board_line,
        confidence=confidence, changed_pixels=900, line_pixels=600, length_px=180.0,
        elongation=12.0, inlier_ratio=0.66, noise_level=1.5, threshold=11.0,
    )


def _line_through(point, heading_deg: float, offset_mm: float = 0.0):
    normal = np.array([math.cos(math.radians(heading_deg + 90)), math.sin(math.radians(heading_deg + 90))])
    return float(normal[0]), float(normal[1]), float(-(normal @ np.asarray(point)) + offset_mm)


def selfcheck() -> int:
    """Assertions that pin the reasoning behind the fusion geometry, since the
    project has no test framework. Run after touching fusion.py or axis.py."""
    from detection import fusion

    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"  {'PASS' if condition else 'FAIL'}  {name}{'  - ' + detail if detail else ''}")
        if not condition:
            failures.append(name)

    print("outlier identifiability with three lines")
    # Three lines through one point, one of them displaced sideways. Whichever
    # one you displace, the leave-one-out pattern is the same up to scale, so
    # nothing in the residuals says which camera moved. This is what
    # ROBUST_MIN_LINES exists for.
    for layout in ([95.0, 105.0, 15.0], [0.0, 60.0, 120.0], [10.0, 70.0, 100.0]):
        shapes = []
        for bad in range(3):
            lines = [_line_through((0.0, 0.0), h, 6.5 if i == bad else 0.0) for i, h in enumerate(layout)]
            per_camera = {i: {"board_line": line} for i, line in enumerate(lines)}
            deltas = np.sort(np.array(sorted(_leave_one_out(per_camera).values())))
            shapes.append(deltas / max(deltas[0], 1e-9))
        spread = max(float(np.max(np.abs(shapes[i] - shapes[0]))) for i in range(3))
        check(f"layout {layout} gives an identical pattern whichever line moved",
              spread < 1e-6, f"max difference {spread:.2e}")

    print("fusion behaviour")
    target = np.array([board_point_t20()[0], board_point_t20()[1]])
    exact = [
        _synthetic_candidate(1, _line_through(target, 10.0), 0.90),
        _synthetic_candidate(2, _line_through(target, 70.0), 0.85),
        _synthetic_candidate(3, _line_through(target, 130.0), 0.88),
    ]
    hit = fuse_axes(exact)
    check("exact lines through a treble 20 still recover it",
          hit.label == "T20" and hit.accepted and math.dist((hit.x_mm, hit.y_mm), target) < 1e-6,
          f"{hit.label} at {hit.x_mm:.6f},{hit.y_mm:.6f}")

    # The event-19 shape: two near-parallel cameras straddling the truth, one
    # of them displaced. Neither may be discarded - the answer has to sit
    # between them, not on top of the more confident one.
    straddle = [
        _synthetic_candidate(1, _line_through(target, 95.0, 0.0), 0.914),
        _synthetic_candidate(2, _line_through(target, 105.0, 6.5), 0.980),
        _synthetic_candidate(3, _line_through(target, 15.0, 0.0), 0.980),
    ]
    hit = fuse_axes(straddle)
    offset = math.dist((hit.x_mm, hit.y_mm), target)
    check("a displaced near-parallel camera is averaged, not locked onto",
          0.4 < offset < 5.5, f"landed {offset:.2f}mm from truth (locking on would be ~6.5mm)")

    check("robust reweighting stays off for three lines", fusion.ROBUST_MIN_LINES > 3,
          f"ROBUST_MIN_LINES={fusion.ROBUST_MIN_LINES}")

    # _robust_refine is dormant at three cameras but still reachable at four,
    # where a badly-placed starting solution can push every line outside
    # ROBUST_TUNING_MM at once. The active set then collapses and lstsq will
    # cheerfully return a point along whichever single line survives. It must
    # degrade to the plain least-squares answer instead.
    lines = np.array([_line_through(target, h, off)[:2] for h, off in
                      ((0.0, 0.0), (50.0, 0.0), (100.0, 0.0), (140.0, 40.0))])
    targets = -np.array([_line_through(target, h, off)[2] for h, off in
                         ((0.0, 0.0), (50.0, 0.0), (100.0, 0.0), (140.0, 40.0))])
    weights = np.full(4, 0.90)
    root = np.sqrt(weights)
    plain = np.linalg.lstsq(lines * root[:, None], targets * root, rcond=None)[0]
    refined = fusion._robust_refine(lines, targets, weights, plain)
    check("a collapsing robust fit falls back instead of running away",
          math.dist(refined, plain) < 1e-9 or math.dist(refined, target) <= math.dist(plain, target),
          f"plain {math.dist(plain, target):.2f}mm -> refined {math.dist(refined, target):.2f}mm from truth")

    print(f"\n{'all checks passed' if not failures else str(len(failures)) + ' FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


def board_point_t20() -> tuple[float, float]:
    from calibration import board_model

    return board_model.board_point_mm(board_model.segment_center_angle_rad(20), 103.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--capture", action="store_true", help="pull a fresh clip library off a running server")
    parser.add_argument("--selfcheck", action="store_true", help="run the fusion geometry assertions and exit")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--json", type=Path, help="also write the raw results here")
    args = parser.parse_args()

    if args.capture:
        capture(args.url, args.library)
        return
    if args.selfcheck:
        raise SystemExit(selfcheck())
    result = run(args.library)
    report(result)
    if args.json:
        serialisable = [
            {k: (v if k != "hit" else (v.to_dict() if v is not None and hasattr(v, "to_dict") else None))
             for k, v in row.items()}
            for row in result["rows"]
        ]
        args.json.write_text(json.dumps(serialisable, indent=1, default=str))


if __name__ == "__main__":
    main()
