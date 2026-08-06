"""Multi-camera detection session: watches 2-3 cameras together, waits for
a synchronized motion-then-settle event, fits an axis per camera and fuses
them into one scored hit. This is what an earlier, single-camera-per-worker
pipeline never had - every score used to come from one camera's unverified
guess, with no cross-camera check at all.

State machine and timing constants ported from the reference implementation
in DART_VISION_HANDOFF.md (see PLAN.md). Simplified relative to that
original in one deliberate way: takeout/"board obstructed" handling here is
a single large-area-change trigger that relearns the baseline, rather than
separate empty-board-reference-matching and scene-clear-wait states tied to
a game engine - this project doesn't have a game engine yet (PLAN.md Phase
3) for those richer states to report into. Worth revisiting once it does.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import deque
from typing import Any

import cv2
import numpy as np

import settings_store
from calibration import store as calibration_store
from calibration.board_model import DETECTION_MASK_MARGIN_MM
from capture.manager import CaptureManager
from events import hub
from leds.controller import led_controller

from . import axis as axis_module
from .fusion import _label_for, fuse_axes
from .models import AxisCandidate, FusedHit
from .scoring import score_board_point, score_point

log = logging.getLogger(__name__)

# Timing is deliberately stability-based, not a fixed short delay - a
# wobbling dart settling too early becomes a second, spurious event, and
# that same wobble can look like the dart being pulled back out.
MOTION_TRIGGER_RATIO = 0.00085
QUIET_BACKGROUND_RATIO = 0.00035
LARGE_CHANGE_RATIO = 0.12  # hand/takeout-sized movement, not a dart
IMPACT_MIN_SETTLE_SECONDS = 0.28
POST_STABLE_RATIO = 0.0008
POST_FRAMES_REQUIRED = 4
POST_EXTRA_CAMERA_GRACE_SECONDS = 0.12
POST_HARD_TIMEOUT_SECONDS = 1.35
COOLDOWN_MIN_SECONDS = 0.45
COOLDOWN_STABLE_SECONDS = 0.20
COOLDOWN_QUIET_RATIO = 0.00055
CAMERA_SYNC_GRACE_SECONDS = 0.045
LOOP_INTERVAL_S = 0.015
BASELINE_ALL_CAMERAS_TIMEOUT_SECONDS = 8.0  # give a slow-starting camera this long before proceeding without it
BASELINE_RELEARN_TIMEOUT_SECONDS = 0.6  # mid-session: cameras are known-good, don't stall play waiting

# The dart-detected LED flash relights the whole board, and the cameras
# can't tell a board-wide lighting change from a dart - left alone it
# reliably fires a phantom detection, twice (once going green, once
# reverting to white). So detection is suppressed across the flash and
# the baseline is relearned afterwards under the restored lighting.
# Keep FLASH_DURATION_S in step with the LED cue's own duration.
# Pulling a dart out looks exactly like throwing one in to a frame-difference
# detector: both leave one dart-shaped patch of changed pixels, and measured
# on real frames a single dart is only 0.64-1.59% of the board ROI - far above
# the 0.085% motion trigger but nowhere near the 12% "hand covering the board"
# threshold. Size alone can't separate them; direction can. Occupancy is
# measured against an empty-board reference, so throwing raises it and
# removing lowers it.
#
# A drop of 0.3% is half the smallest single-dart signature observed, which
# clears measured idle-camera noise (~0.00%) by a wide margin.
TAKEOUT_OCCUPANCY_DROP = 0.003
EMPTY_BOARD_OCCUPANCY = 0.002  # at or below this the board is treated as clear again
# Three darts cover roughly 3% of the board, so anything past this can't be
# darts - the reference has drifted (lighting, a nudged camera, someone
# standing in shot). Left unchecked it poisons every later comparison:
# occupancy sits permanently high and ordinary noise reads as a big drop,
# so real throws get thrown away as takeouts and the board goes quiet.
MAX_PLAUSIBLE_OCCUPANCY = 0.06

# Once the engine is waiting for the darts to come out, the turn is full and a
# new dart CANNOT be scored - so anything substantial moving on the board is
# the takeout, and there is no longer a dart/hand ambiguity to resolve. The
# detector was applying its careful "is this a dart or a hand?" thresholds to a
# question that no longer had two answers, which is why a takeout that didn't
# happen to sweep 12% of two cameras got missed.
AWAITING_TAKEOUT_CHANGE_RATIO = 0.02

# After a takeout, wait for the board to be genuinely still before watching for
# darts again. Darts usually come out one at a time: without this, pulling the
# first fires the takeout and advances the turn, and pulling the second and
# third then look like the NEXT player throwing - which either scores phantom
# darts or fires a second takeout and skips a player entirely.
#
# Stillness is only the gate on *measuring* occupancy, though - a hand held over
# the board is perfectly still, and so is a board with darts left in it. See
# _clearing. There is deliberately no timeout that gives up and advances the
# turn anyway: a turn must never move on while darts are still in the board, and
# the "Darts removed" button is always available for a dart that is genuinely
# stuck. The only escape hatch is MAX_PLAUSIBLE_OCCUPANCY, for when the
# reference itself has gone bad and "empty" is unreachable.
CLEARING_QUIET_RATIO = 0.0015
CLEARING_STABLE_SECONDS = 0.8

FLASH_DURATION_S = 0.5
FLASH_SETTLE_SECONDS = 0.35  # margin for the revert to land and the sensors to re-expose
FLASH_SUPPRESS_SECONDS = FLASH_DURATION_S + FLASH_SETTLE_SECONDS

# Takeout tracing. Off unless explicitly started, because it costs an extra
# _quick_change_ratio per camera per sample - the loop's own comparisons are
# against the baseline only, and the point of the trace is to also see the two
# signals the running detector never measures continuously: distance from the
# EMPTY-board reference, and frame-to-frame movement, in every state rather
# than only when an event is being analysed.
TRACE_INTERVAL_S = 0.1
TRACE_MAX_SAMPLES = 4000  # ~6.5 minutes at TRACE_INTERVAL_S


def _median_frame(frames: "deque[np.ndarray] | list[np.ndarray]") -> np.ndarray:
    values = list(frames)
    if len(values) == 1:
        return values[0].copy()
    return np.median(np.stack(values, axis=0), axis=0).astype(np.uint8)


def _quick_change_ratio(first: np.ndarray, second: np.ndarray, roi: np.ndarray) -> float:
    """Adaptive per-frame noise threshold (median absolute deviation), not a
    fixed pixel-diff cutoff - camera sensor noise varies with lighting/gain,
    and a fixed threshold either misses real motion or false-triggers on
    noise depending on conditions at the time. Same technique as axis.py's
    dart-isolation threshold, applied here to the cheaper trigger/settle
    decision."""
    gray_a = cv2.GaussianBlur(cv2.cvtColor(first, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    gray_b = cv2.GaussianBlur(cv2.cvtColor(second, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    difference = cv2.absdiff(gray_a, gray_b)
    values = difference[roi > 0]
    if not values.size:
        return 0.0
    median = float(np.median(values))
    mad = float(np.median(np.abs(values.astype(np.float32) - median)))
    threshold = max(13.0, median + 5.0 * 1.4826 * mad)
    return float(np.mean(values > threshold))


class DetectionSession:
    def __init__(self, camera_ids: list[int], manager: CaptureManager, loop: asyncio.AbstractEventLoop):
        self.camera_ids = camera_ids
        self._manager = manager
        self._loop = loop
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="detect-session", daemon=True)

        self.state = "stopped"
        self.message = "Detector is stopped"
        self._state_since = time.monotonic()
        self._lock = threading.RLock()

        self._pre_buffers: dict[int, deque[np.ndarray]] = {cid: deque(maxlen=7) for cid in camera_ids}
        self._baselines: dict[int, np.ndarray] = {}
        self._frozen_pre: dict[int, np.ndarray] = {}
        self._post_buffers: dict[int, deque[np.ndarray]] = {cid: deque(maxlen=6) for cid in camera_ids}
        self._post_last_frames: dict[int, np.ndarray] = {}
        self._post_two_ready_at: float | None = None
        self._triggered_at = 0.0
        self._cooldown_buffers: dict[int, deque[np.ndarray]] = {cid: deque(maxlen=5) for cid in camera_ids}
        self._cooldown_last_frames: dict[int, np.ndarray] = {}
        self._cooldown_stable_since: float | None = None
        self._roi_cache: dict[int, tuple[tuple[int, int], np.ndarray]] = {}

        self._last_seen_frame_no: dict[int, int] = {}
        self._pending_frames: dict[int, np.ndarray] = {}
        self._pending_since: float | None = None
        self._suppress_until: float | None = None
        # "clearing" state bookkeeping - set properly by _begin_clearing, but
        # initialised here so no path into _clearing can raise in the detection
        # thread, which would take the detector down with it.
        self._clear_last_frames: dict[int, np.ndarray] = {}
        self._clear_stable_since: float | None = None
        self._clearing_started: float = 0.0
        self._takeout_pending = False
        self._takeout_was_awaiting = False
        self._takeouts: deque[dict] = deque(maxlen=25)
        self._takeout_seq = 0
        self._streams: dict[int, Any] = {}  # live CameraStreams, for health reporting
        # Empty-board reference and how far the board currently differs from
        # it, which is what makes "dart removed" distinguishable from
        # "dart thrown" - see TAKEOUT_OCCUPANCY_DROP.
        self._reference: dict[int, np.ndarray] = {}
        self._occupancy = 0.0
        self._established: list[int] = []  # cameras proven working at session start

        # The enriched payload dict (the same object stored in _history and
        # broadcast over the WebSocket), NOT the bare FusedHit: only the
        # payload carries event_id/correction/evidence_cameras, and a client
        # that reads this from /status must be able to correct the throw
        # exactly like one that received it live.
        self.latest_hit: dict[str, Any] | None = None
        self.latest_candidates: dict[int, AxisCandidate] = {}
        # Bounded history so a "why was this throw wrong" question can be
        # answered after the fact - the single latest_hit above isn't enough
        # once a second dart lands before anyone looks.
        self._history: deque[dict[str, Any]] = deque(maxlen=40)
        # A photo of each throw, so a correction can be placed on the actual
        # frame the dart landed in rather than a live re-stream that's since
        # moved on. Kept for far fewer events than _history (images are
        # heavier) with its own explicit eviction, since deque(maxlen=...)
        # only bounds _evidence_order itself, not the dict it indexes.
        self._event_seq = 0
        self._evidence: dict[int, dict[int, bytes]] = {}
        self._evidence_order: deque[int] = deque()  # no maxlen: eviction needs the dropped id, see below
        # Matches the history length. 15 was too short in practice: a wrong
        # call gets reported after a few more throws have landed, by which
        # point the frames needed to diagnose it had already been evicted.
        # ~3 JPEGs x 140KB x 40 is around 17MB, which is affordable.
        self._evidence_max = 40

        # Takeout trace - see TRACE_INTERVAL_S. None means "not tracing".
        self._trace: deque[dict[str, Any]] | None = None
        self._trace_started = 0.0
        self._trace_last_sample = 0.0
        self._trace_last_frames: dict[int, np.ndarray] = {}

    # ------------------------------------------------------------ tracing

    def start_trace(self) -> dict[str, Any]:
        with self._lock:
            self._trace = deque(maxlen=TRACE_MAX_SAMPLES)
            self._trace_started = time.monotonic()
            self._trace_last_sample = 0.0
            self._trace_last_frames = {}
        self._trace_mark("trace.start")
        return {"tracing": True}

    def stop_trace(self) -> dict[str, Any]:
        self._trace_mark("trace.stop")
        with self._lock:
            samples = len(self._trace) if self._trace is not None else 0
            self._trace_last_frames = {}
        return {"tracing": False, "samples": samples}

    def trace_dump(self) -> dict[str, Any]:
        with self._lock:
            if self._trace is None:
                return {"tracing": False, "samples": []}
            return {
                "tracing": True,
                "camera_ids": self.camera_ids,
                "constants": {
                    "MOTION_TRIGGER_RATIO": MOTION_TRIGGER_RATIO,
                    "LARGE_CHANGE_RATIO": LARGE_CHANGE_RATIO,
                    "AWAITING_TAKEOUT_CHANGE_RATIO": AWAITING_TAKEOUT_CHANGE_RATIO,
                    "TAKEOUT_OCCUPANCY_DROP": TAKEOUT_OCCUPANCY_DROP,
                    "EMPTY_BOARD_OCCUPANCY": EMPTY_BOARD_OCCUPANCY,
                    "MAX_PLAUSIBLE_OCCUPANCY": MAX_PLAUSIBLE_OCCUPANCY,
                    "CLEARING_QUIET_RATIO": CLEARING_QUIET_RATIO,
                    "CLEARING_STABLE_SECONDS": CLEARING_STABLE_SECONDS,
                },
                "samples": list(self._trace),
            }

    def _trace_mark(self, kind: str, **fields: Any) -> None:
        """Record a discrete event into the same stream as the samples, so the
        timeline reads in order without having to merge two lists."""
        with self._lock:
            if self._trace is None:
                return
            self._trace.append({
                "t": round(time.monotonic() - self._trace_started, 3),
                "mark": kind, "state": self.state, **fields,
            })

    def _trace_sample(self, now: float, frames: dict[int, np.ndarray], suppressed: bool) -> None:
        """One row of every signal the takeout decision could possibly use.

        Deliberately measures more than the detector does: `reference` (how far
        the board is from EMPTY) is currently only computed inside
        _analyse_event, so nobody has ever seen its shape during a removal,
        which is exactly the gap this is meant to close.
        """
        if self._trace is None or now - self._trace_last_sample < TRACE_INTERVAL_S:
            return
        self._trace_last_sample = now

        per_camera: dict[str, dict[str, float]] = {}
        occupancy_values = []
        for cid, frame in frames.items():
            roi = self._roi(cid, frame)
            entry: dict[str, float] = {}
            baseline = self._baselines.get(cid)
            if baseline is not None and baseline.shape == frame.shape:
                entry["baseline"] = round(_quick_change_ratio(baseline, frame, roi), 5)
            reference = self._reference.get(cid)
            if reference is not None and reference.shape == frame.shape:
                value = _quick_change_ratio(reference, frame, roi)
                entry["reference"] = round(value, 5)
                occupancy_values.append(value)
            previous = self._trace_last_frames.get(cid)
            if previous is not None and previous.shape == frame.shape:
                entry["previous"] = round(_quick_change_ratio(previous, frame, roi), 5)
            self._trace_last_frames[cid] = frame.copy()
            per_camera[str(cid)] = entry

        with self._lock:
            self._trace.append({
                "t": round(now - self._trace_started, 3),
                "state": self.state,
                "suppressed": suppressed,
                "awaiting": self._game_awaiting_takeout(),
                # What the board actually looks like right now...
                "occupancy": round(float(np.mean(occupancy_values)), 5) if occupancy_values else None,
                # ...versus the value the takeout test in _analyse_event
                # compares against, which is only ever updated there.
                "stored_occupancy": round(self._occupancy, 5),
                "cameras": per_camera,
            })

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.state = "learning_baseline"
        self.message = "Learning a stable board image"
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        for cid in self.camera_ids:
            self._manager.release(cid)
        with self._lock:
            self.state = "stopped"
            self.message = "Detector is stopped"

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": True,
                "state": self.state,
                "message": self.message,
                "camera_ids": self.camera_ids,
                "baseline_cameras": sorted(self._baselines),
                "latest_hit": self.latest_hit,
                "latest_candidates": {
                    cid: candidate.to_dict() for cid, candidate in self.latest_candidates.items()
                },
                "history": list(self._history),
                "camera_health": {cid: s.health() for cid, s in self._streams.items()},
                "board_occupancy": self._occupancy,
                # Diagnostics for the Detection screen: takeout is the one
                # event with no dart to show for it, so without this there is
                # nothing to look at when it fires - or when it doesn't.
                "takeouts": list(self._takeouts),
                "game_awaiting_takeout": self._game_awaiting_takeout(),
            }

    # ------------------------------------------------------------ correction / analysis

    def get_evidence_frame(self, event_id: int, camera_id: int) -> bytes | None:
        with self._lock:
            return self._evidence.get(event_id, {}).get(camera_id)

    def apply_correction(self, event_id: int, camera_id: int, x_px: float, y_px: float) -> dict | None:
        """Records where a throw *actually* landed, scored from a manually
        placed point on that camera's own saved evidence frame - reuses the
        same image-px -> board-mm -> score path as everything else, so a
        correction is directly comparable to what the pipeline computed."""
        profile = calibration_store.get(camera_id)
        if profile is None:
            return None
        scored = score_point((x_px, y_px), profile["homography"])
        return self._store_correction(event_id, scored, camera_id=camera_id, x_px=x_px, y_px=y_px)

    def apply_board_correction(self, event_id: int, x_mm: float, y_mm: float) -> dict | None:
        """Same as apply_correction, but for a click placed directly on the
        front-on board diagram - already in board space, so no camera or
        homography is involved at all. Works even for an event whose photo
        evidence has since aged out of _evidence's small retention window."""
        scored = score_board_point(x_mm, y_mm)
        scored["board_point"] = [x_mm, y_mm]
        return self._store_correction(event_id, scored)

    def _store_correction(
        self, event_id: int, scored: dict, camera_id: int | None = None,
        x_px: float | None = None, y_px: float | None = None,
    ) -> dict | None:
        correction = {
            "camera_id": camera_id,
            "x_px": x_px,
            "y_px": y_px,
            "x_mm": scored["board_point"][0],
            "y_mm": scored["board_point"][1],
            "segment": scored["segment"],
            "multiplier": scored["multiplier"],
            "score": scored["value"],
            "ring": scored["ring"],
            "label": _label_for(scored),
            "corrected_at": time.time(),
        }
        with self._lock:
            for entry in self._history:
                if entry.get("event_id") == event_id:
                    entry["correction"] = correction
                    return entry
        return None

    def analyze(self) -> dict[str, Any]:
        """Compares every corrected throw against what the pipeline actually
        detected - the concrete, measurable version of "eyeball the
        calibration grid and guess," now that corrections exist to compare
        against instead of just the detected label."""
        with self._lock:
            corrected = [dict(entry) for entry in self._history if entry.get("correction")]

        per_event = []
        errors_mm = []
        segment_mismatches = 0
        mismatches_flagged = 0
        mismatches_missed = 0
        for entry in corrected:
            correction = entry["correction"]
            detected_segment = entry.get("segment")
            detected_multiplier = entry.get("multiplier")
            wrong = detected_segment != correction["segment"] or detected_multiplier != correction["multiplier"]
            error_mm = None
            if entry.get("x_mm") is not None:
                error_mm = math.hypot(entry["x_mm"] - correction["x_mm"], entry["y_mm"] - correction["y_mm"])
                errors_mm.append(error_mm)
            if wrong:
                segment_mismatches += 1
                if entry.get("score_uncertain"):
                    mismatches_flagged += 1
                else:
                    mismatches_missed += 1
            per_event.append({
                "event_id": entry.get("event_id"),
                "ts": entry.get("ts"),
                "detected_label": entry.get("label"),
                "actual_label": correction["label"],
                "wrong": wrong,
                "error_mm": error_mm,
                "wire_distance_mm": entry.get("wire_distance_mm"),
                "positional_uncertainty_mm": entry.get("positional_uncertainty_mm"),
                "score_uncertain": entry.get("score_uncertain"),
                "cameras_used": entry.get("cameras_used"),
            })

        return {
            "count": len(corrected),
            "segment_mismatches": segment_mismatches,
            "mismatches_flagged_uncertain": mismatches_flagged,
            "mismatches_not_flagged": mismatches_missed,
            "mean_error_mm": (sum(errors_mm) / len(errors_mm)) if errors_mm else None,
            "max_error_mm": max(errors_mm) if errors_mm else None,
            "events": per_event,
        }

    # ------------------------------------------------------------ helpers

    def _broadcast(self, message: dict) -> None:
        try:
            asyncio.run_coroutine_threadsafe(hub.broadcast(message), self._loop)
        except RuntimeError:
            pass

    def _set_state(self, state: str, message: str) -> None:
        with self._lock:
            changed = self.state != state
            if changed:
                self._state_since = time.monotonic()
            self.state = state
            self.message = message
        if changed:
            self._trace_mark("state", to=state, message=message)

    def _roi(self, camera_id: int, frame: np.ndarray) -> np.ndarray:
        profile = calibration_store.get(camera_id)
        if profile is None:
            return np.zeros(frame.shape[:2], dtype=np.uint8)
        shape = frame.shape[:2]
        cached = self._roi_cache.get(camera_id)
        if cached is not None and cached[0] == shape:
            return cached[1]
        board_to_image = np.linalg.inv(np.array(profile["homography"], dtype=np.float64))
        mask = axis_module.board_mask(shape, board_to_image, margin_mm=DETECTION_MASK_MARGIN_MM)
        self._roi_cache[camera_id] = (shape, mask)
        return mask

    # ------------------------------------------------------------ main loop

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_unprotected()
            except Exception as exc:  # a detector crash must never leave the UI stuck
                log.exception("detection session error, recovering")
                with self._lock:
                    self.message = f"Detector recovered from {type(exc).__name__} - relearning the board"
                self._reset_to_baseline()
                self._stop.wait(0.25)

    def _feed_game(self, hit: FusedHit) -> None:
        """Hand a scored dart to whatever game is running.

        Imported lazily and wrapped: detection must keep working perfectly
        well with no game in progress, and a bug in a game must never take
        the detector down with it.
        """
        try:
            from games.base import Dart
            from games.engine import match_engine

            match_engine.submit_dart(Dart(
                segment=hit.segment,
                multiplier=hit.multiplier,
                score=hit.score,
                label=hit.label,
                x_mm=hit.x_mm,
                y_mm=hit.y_mm,
            ))
        except Exception:
            log.exception("game engine rejected a dart; detection continues")

    def _notify_game_takeout(self) -> None:
        """Darts pulled out of the board means the turn is over.

        The engine's own turn.start cue is suppressed here - _handle_takeout
        has already fired the red takeout flash, and the blue comet would
        simply overwrite it.
        """
        try:
            from games.engine import match_engine

            match_engine.next_turn(cue=False)
        except Exception:
            log.exception("game engine failed to advance the turn")

    def _game_awaiting_takeout(self) -> bool:
        """Is the engine waiting for the darts to come out?

        Knowing this collapses the hardest question the detector has - "is that
        a dart landing or a hand reaching in?" - into one with a single answer,
        because the turn is full and no further dart can be scored. Same
        lazily-imported, exception-wrapped shape as the other engine hooks, so
        detection keeps working with no game running.
        """
        try:
            from games.engine import match_engine

            return bool(match_engine.awaiting_takeout)
        except Exception:
            return False

    def _begin_clearing(self) -> None:
        self._clear_last_frames = {}
        self._clear_stable_since = None
        self._clearing_started = time.monotonic()
        # Whether this takeout still owes the game a turn advance. See
        # _clearing: the advance happens when the board is confirmed EMPTY, not
        # when the hand first appears.
        self._takeout_pending = True
        self._takeout_was_awaiting = self._game_awaiting_takeout()
        self._set_state("clearing", "Darts coming out - waiting for the board to clear")

    def _clearing(self, frames: dict[int, np.ndarray]) -> None:
        """Hold until the board is still AND actually empty, then advance.

        Stillness alone used to be the whole test, and it is not enough twice
        over. A hand held over the board for CLEARING_STABLE_SECONDS - reaching
        past it, or deciding which dart to grab first - reads as perfectly
        still, and so does a board with two of the three darts still in it. The
        detector would relearn its baseline with darts (or a hand) in shot and
        go back to `ready`, and the darts coming out afterwards then looked like
        a fresh event: measured on a real game, one dart left behind produced a
        second takeout the moment it was finally pulled, advancing the turn
        twice and skipping a player.

        So stillness is now only the *gate* on measuring: a hand in shot makes
        occupancy meaningless, so wait for it to leave, then ask the question
        that actually matters - is the board clear? Occupancy is measured
        against the empty-board reference, and EMPTY_BOARD_OCCUPANCY sits an
        order of magnitude below one dart (a single dart measures ~1.0-1.5% of
        the board on this rig, an empty board 0.00-0.05%), so the two are not
        close to confusable.

        Nothing is scored and no further takeout fires while this runs, so
        pulling three darts one at a time is still a single takeout.
        """
        now = time.monotonic()
        moving = False
        for cid, frame in frames.items():
            previous = self._clear_last_frames.get(cid)
            if previous is not None and previous.shape == frame.shape:
                if _quick_change_ratio(previous, frame, self._roi(cid, frame)) > CLEARING_QUIET_RATIO:
                    moving = True
            self._clear_last_frames[cid] = frame.copy()

        if moving:
            self._clear_stable_since = None
            return
        if self._clear_stable_since is None:
            self._clear_stable_since = now
            return
        if now - self._clear_stable_since < CLEARING_STABLE_SECONDS:
            return

        # Still. Now - and only now - is occupancy worth reading.
        occupancy = self._board_occupancy(frames)
        waited = now - self._clearing_started
        if occupancy is None:
            self._finish_clearing(occupancy)
            return

        if occupancy > MAX_PLAUSIBLE_OCCUPANCY:
            # More of the board differs from the reference than three darts
            # ever could, so the reference itself is wrong (lighting moved, a
            # camera was nudged, someone is standing in shot). Waiting for
            # "empty" against a broken reference would wedge the detector shut
            # for the rest of the night, so re-anchor and carry on.
            log.warning("clearing: reference looks stale (%.2f%% differs) - re-anchoring", occupancy * 100)
            self._reference = {cid: frame.copy() for cid, frame in frames.items()}
            self._occupancy = 0.0
            self._finish_clearing(0.0)
            return

        board_clear = occupancy <= EMPTY_BOARD_OCCUPANCY
        # Only hold out for an empty board when a turn advance is actually at
        # stake - i.e. the turn was full when the darts started coming out.
        #
        # Mid-turn the player still has darts to throw, and a "takeout" there is
        # usually a hand reaching past the board rather than a real removal.
        # Holding would block the rest of their turn, which is worse than the
        # bug this is fixing, so stillness alone is enough to resume. The turn
        # still only advances if the board genuinely emptied, so a hand reaching
        # past no longer moves the game on at all.
        if not board_clear and self._takeout_was_awaiting:
            self._trace_mark("clearing_waiting", occupancy=round(occupancy, 5), waited=round(waited, 1))
            self._set_state(
                "clearing",
                f"Darts still in the board ({occupancy * 100:.1f}% covered) - "
                f"waiting, or press Darts removed",
            )
            self._clear_stable_since = now  # re-arm, so this re-checks periodically
            return

        self._finish_clearing(occupancy, advance=board_clear)

    def _finish_clearing(self, occupancy: float | None, advance: bool = True) -> None:
        """Stop holding and start watching the board again.

        The turn advance lives here rather than in _handle_takeout because the
        game must not move on while darts are still in the board - that is the
        whole point of the rework. It is skipped when the board never emptied
        (so a hand reaching past the board no longer costs anyone a turn), and
        when the human pressed "Darts removed" while we were waiting, which is
        the one way this could otherwise advance twice.
        """
        # If the turn was full when the darts came out and it no longer is,
        # someone confirmed the takeout by hand while we were waiting, and the
        # engine has already moved on.
        confirmed_by_hand = self._takeout_was_awaiting and not self._game_awaiting_takeout()
        will_advance = bool(self._takeout_pending and advance and not confirmed_by_hand)
        self._trace_mark(
            "clearing_done",
            occupancy=None if occupancy is None else round(occupancy, 5),
            pending=self._takeout_pending, advance=advance,
            confirmed_by_hand=confirmed_by_hand, advancing=will_advance,
        )
        self._takeout_pending = False
        if will_advance:
            self._notify_game_takeout()
        self._reset_to_baseline()

    def _handle_takeout(self, camera_ids: list[int], reason: str) -> None:
        """One place for everything a takeout triggers, however it was spotted.

        There are three independent ways to notice darts coming out (a large
        change while watching, an event that analysed as a big scene change,
        and a fall in board occupancy) and they used to each repeat this by
        hand, which is how they drifted apart.

        The red flash needs the same detection blinding as the dart-detected
        flash, and here it matters more: a takeout always relearns the
        baseline, so without suppression the reference would be captured under
        red light and the revert to white would instantly read as a phantom
        throw. Suppression is armed *before* the relearn, and the main loop
        drops frames while it holds, so learning only starts once the board is
        back to its calibrated white.

        Note what this deliberately does NOT do: advance the turn. Detecting a
        hand is not the same as the darts being out, and acting on the first
        sign of movement is what let a hand reaching past the board, or a
        removal that paused halfway, move the game on. The advance happens in
        _finish_clearing once the board is confirmed empty.
        """
        log.info("takeout on cameras %s (%s)", camera_ids, reason)
        self._trace_mark(
            "takeout", reason=reason, camera_ids=camera_ids,
            awaiting=self._game_awaiting_takeout(), stored_occupancy=round(self._occupancy, 5),
        )
        self._takeout_seq += 1
        event = {
            "id": self._takeout_seq,
            "camera_ids": camera_ids,
            "reason": reason,
            "awaiting": self._game_awaiting_takeout(),
            "occupancy": round(self._occupancy * 100, 3),
            "ts": time.time(),
        }
        with self._lock:
            self._takeouts.appendleft(event)
        self._broadcast({"type": "detection.takeout", **event})
        led_controller.flash_cue("takeout", duration_s=FLASH_DURATION_S)
        self._suppress_until = time.monotonic() + FLASH_SUPPRESS_SECONDS
        # Not straight back to learning a baseline: the other darts are
        # probably still coming out. Wait for the board to settle first.
        self._begin_clearing()

    def _board_occupancy(self, frames: dict[int, np.ndarray]) -> float | None:
        """How far the board differs from the empty-board reference, averaged
        over the cameras that have one. Rises as darts land, falls as they're
        pulled out - the signal that tells a takeout from a throw."""
        values = []
        for cid, frame in frames.items():
            reference = self._reference.get(cid)
            if reference is None or reference.shape != frame.shape:
                continue
            values.append(_quick_change_ratio(reference, frame, self._roi(cid, frame)))
        return float(np.mean(values)) if values else None

    def _note_occupancy(self, occupancy: float | None) -> None:
        if occupancy is None:
            return
        self._occupancy = occupancy
        # Board is clear again - re-anchor the reference so slow drift in
        # lighting or camera position can't accumulate across a whole night.
        if occupancy <= EMPTY_BOARD_OCCUPANCY:
            self._reference = {cid: frame.copy() for cid, frame in self._baselines.items()}
            self._occupancy = 0.0

    def _flash_suppressed(self, now: float) -> bool:
        """True while the dart-detected LED flash is altering the scene.

        Frames shot mid-flash are dropped, but NO baseline relearn is needed
        afterwards: _analyse_event already stored the post-impact frames as
        the new baseline, and those were captured before the flash under the
        same white light the strip reverts to - so they are already the
        correct reference for the next throw, dart included. Relearning here
        used to cost a full learning_baseline cycle (5 frames from every
        camera, up to an 8s wait) after *every single dart*, which is what
        made the board go quiet between throws.

        Since the LED controller now reports when the board is being relit
        (LedController.lighting_busy_until), this covers cues fired from
        anywhere - including the game engine's, which previously reached the
        cameras completely unguarded and made crowning a Killer look like a
        takeout. The detector's own _suppress_until is kept for the case where
        no cue reached the strip at all (unconfigured or undefined cue), where
        there is no lighting change to hide from but the post-flash settle is
        still wanted.
        """
        busy_until = led_controller.lighting_busy_until
        if self._suppress_until is not None:
            if now >= self._suppress_until:
                self._suppress_until = None
            else:
                busy_until = max(busy_until, self._suppress_until)
        return now < busy_until

    def _reset_to_baseline(self) -> None:
        for buf in self._pre_buffers.values():
            buf.clear()
        self._baselines = {}
        self._set_state("learning_baseline", "Learning a stable board image")

    def _run_unprotected(self) -> None:
        capture = settings_store.load()["capture"]
        streams = {
            cid: self._manager.acquire(cid, capture["width"], capture["height"], capture["fps"])
            for cid in self.camera_ids
        }
        self._streams = streams
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                new_frames: dict[int, np.ndarray] = {}
                for cid, stream in streams.items():
                    jpeg, frame_no = stream.peek()
                    if jpeg is None or frame_no <= self._last_seen_frame_no.get(cid, 0):
                        continue
                    arr = np.frombuffer(jpeg, dtype=np.uint8)
                    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if bgr is None:
                        continue
                    self._last_seen_frame_no[cid] = frame_no
                    new_frames[cid] = bgr
                    if not self._pending_frames:
                        self._pending_since = now
                    self._pending_frames[cid] = bgr

                pending_age = now - (self._pending_since or now)
                all_arrived = len(self._pending_frames) >= len(self.camera_ids)
                if len(self._pending_frames) < 2 or (not all_arrived and pending_age < CAMERA_SYNC_GRACE_SECONDS):
                    self._stop.wait(LOOP_INTERVAL_S)
                    continue

                frames = self._pending_frames
                self._pending_frames = {}
                self._pending_since = None

                # Discard frames shot under changing LED light entirely -
                # they'd only be misread as motion.
                suppressed = self._flash_suppressed(now)
                # Sampled before the drop, and told whether the frame was
                # dropped: the blind window lands right where a hand is busiest,
                # so a trace that silently skipped it would hide the gap.
                self._trace_sample(now, frames, suppressed)
                if suppressed:
                    self._stop.wait(LOOP_INTERVAL_S)
                    continue

                if self.state in {"stopped", "learning_baseline"}:
                    self._learn_baseline(frames)
                elif self.state == "clearing":
                    self._clearing(frames)
                elif self.state == "ready":
                    self._watch_for_event(frames)
                elif self.state == "settling":
                    self._collect_post_frames(frames)
                elif self.state == "cooldown":
                    self._cooldown(frames)
                self._stop.wait(LOOP_INTERVAL_S)
        finally:
            for cid in self.camera_ids:
                self._manager.release(cid)

    # ------------------------------------------------------------ states

    def _learn_baseline(self, frames: dict[int, np.ndarray]) -> None:
        for cid, frame in frames.items():
            self._pre_buffers[cid].append(frame)
        ready = [cid for cid in self.camera_ids if len(self._pre_buffers[cid]) >= 5]
        # Only the first learn of a session waits the full timeout for a
        # camera that may still be warming up. Once we know which cameras
        # actually work, later relearns (after a takeout) wait only for
        # those, so one dead camera can't add 8s of blindness every time.
        expected = self._established or self.camera_ids
        all_ready = all(cid in ready for cid in expected)
        elapsed = time.monotonic() - self._state_since
        timeout = BASELINE_ALL_CAMERAS_TIMEOUT_SECONDS if not self._established else BASELINE_RELEARN_TIMEOUT_SECONDS
        if not all_ready and (len(ready) < 2 or elapsed < timeout):
            self._set_state(
                "learning_baseline",
                f"Learning a stable board image ({len(ready)}/{len(self.camera_ids)} cameras ready)",
            )
            return
        self._baselines = {cid: _median_frame(self._pre_buffers[cid]) for cid in ready}
        # Captured once per session, not on every relearn (the baseline is
        # relearned after each dart, and re-capturing here would silently
        # redefine "empty" as "however many darts are in the board now").
        if not self._reference:
            self._reference = {cid: self._baselines[cid].copy() for cid in ready}
            self._occupancy = 0.0
        if not self._established:
            self._established = list(ready)
        # The stored occupancy has to describe the board this baseline was just
        # taken from, and until now nothing updated it except _analyse_event.
        # Three of the four takeout paths return before reaching that, so after
        # a takeout it kept the *pre-removal* value - and the first event
        # analysed on the now-empty board read as an enormous drop and fired a
        # SECOND takeout, advancing the turn again and skipping a player who
        # never threw. It happened on every removal of a real game:
        #   2.92% -> 0.03%,  1.99% -> 0.02%,  1.49% -> 0.00%
        # each one a genuine takeout immediately followed by a phantom one.
        self._note_occupancy(self._board_occupancy(self._baselines))
        if all_ready:
            self._set_state("ready", "Board stable - waiting for a dart")
        else:
            missing = [cid for cid in self.camera_ids if cid not in ready]
            self._set_state(
                "ready",
                f"Board stable, but camera {', '.join(str(c) for c in missing)} never caught up - "
                "proceeding without it. Check its connection.",
            )

    def _watch_for_event(self, frames: dict[int, np.ndarray]) -> None:
        ratios: dict[int, float] = {}
        for cid, frame in frames.items():
            baseline = self._baselines.get(cid)
            if baseline is None or baseline.shape != frame.shape:
                continue
            ratios[cid] = _quick_change_ratio(baseline, frame, self._roi(cid, frame))

        # A hand reaching in from one side does not always sweep 12% of two
        # cameras, so when the engine is already waiting for the darts the bar
        # drops to 2% - there is no dart it could be confused with.
        awaiting = self._game_awaiting_takeout()
        threshold = AWAITING_TAKEOUT_CHANGE_RATIO if awaiting else LARGE_CHANGE_RATIO
        large = [cid for cid, ratio in ratios.items() if ratio > threshold]
        if len(large) >= 2:
            self._handle_takeout(
                large,
                "movement while waiting for the darts" if awaiting
                else "hand-sized movement across the board",
            )
            return

        triggers = [cid for cid, ratio in ratios.items() if ratio > MOTION_TRIGGER_RATIO]
        if len(triggers) >= 2:
            self._frozen_pre = {cid: f.copy() for cid, f in self._baselines.items()}
            for buf in self._post_buffers.values():
                buf.clear()
            self._post_last_frames = {}
            self._post_two_ready_at = None
            self._triggered_at = time.monotonic()
            self._set_state("settling", "Impact detected - waiting for the dart to settle")
            return

        if ratios and all(ratio <= QUIET_BACKGROUND_RATIO for ratio in ratios.values()):
            for cid, frame in frames.items():
                if cid in self._baselines:
                    self._baselines[cid] = cv2.addWeighted(self._baselines[cid], 0.98, frame, 0.02, 0.0)

    def _collect_post_frames(self, frames: dict[int, np.ndarray]) -> None:
        now = time.monotonic()
        elapsed = now - self._triggered_at
        if elapsed < IMPACT_MIN_SETTLE_SECONDS:
            for cid, frame in frames.items():
                if cid in self._frozen_pre:
                    self._post_last_frames[cid] = frame.copy()
            return

        for cid, frame in frames.items():
            if cid not in self._frozen_pre:
                continue
            previous = self._post_last_frames.get(cid)
            stable = (
                previous is not None
                and previous.shape == frame.shape
                and _quick_change_ratio(previous, frame, self._roi(cid, frame)) <= POST_STABLE_RATIO
            )
            if not stable and self._post_buffers[cid]:
                self._post_buffers[cid].clear()
            self._post_buffers[cid].append(frame.copy())
            self._post_last_frames[cid] = frame.copy()

        expected = [cid for cid in self.camera_ids if cid in self._frozen_pre]
        complete = [cid for cid in expected if len(self._post_buffers[cid]) >= POST_FRAMES_REQUIRED]

        if len(complete) >= 2 and len(complete) == len(expected):
            self._analyse_event(complete)
            return
        if len(complete) >= 2:
            if self._post_two_ready_at is None:
                self._post_two_ready_at = now
            elif now - self._post_two_ready_at >= POST_EXTRA_CAMERA_GRACE_SECONDS:
                self._analyse_event(complete)
                return
        else:
            self._post_two_ready_at = None

        if elapsed >= POST_HARD_TIMEOUT_SECONDS:
            fallback = []
            for cid in expected:
                if not self._post_buffers[cid] and cid in self._post_last_frames:
                    self._post_buffers[cid].append(self._post_last_frames[cid])
                if self._post_buffers[cid]:
                    fallback.append(cid)
            self._analyse_event(fallback)

    def _analyse_event(self, camera_ids: list[int]) -> None:
        if len(camera_ids) < 2:
            self._reset_to_baseline()
            return

        # The turn is full, so whatever just settled cannot be a dart being
        # scored. Pulling a dart OUT leaves the same single dart-shaped patch
        # of changed pixels as throwing one IN, so this used to fuse into a
        # confident hit, get handed to the engine, and be dropped there for
        # having no darts left - a takeout that looked, from the board, like
        # nothing happened at all. That is the miss this is fixing.
        if self._game_awaiting_takeout():
            self._handle_takeout(camera_ids, "board changed while waiting for the darts to come out")
            return

        candidates: list[AxisCandidate] = []
        changed_ratios: dict[int, float] = {}
        camera_notes: dict[int, str] = {}
        post_frames: dict[int, np.ndarray] = {}
        for cid in camera_ids:
            pre = self._frozen_pre[cid]
            post = _median_frame(self._post_buffers[cid])
            post_frames[cid] = post
            profile = calibration_store.get(cid)
            if profile is None:
                continue
            image_to_board = np.array(profile["homography"], dtype=np.float64)
            board_to_image = np.linalg.inv(image_to_board)
            analysis = axis_module.detect_dart_axis(cid, pre, post, image_to_board, board_to_image)
            changed_ratios[cid] = analysis.changed_ratio
            if analysis.candidate is not None:
                candidates.append(analysis.candidate)
            else:
                # Keep why a camera dropped out - otherwise it silently
                # vanishes from cameras_used and a 2-camera result looks
                # identical to one where the third camera never saw
                # anything, which makes wrong calls hard to diagnose.
                camera_notes[cid] = analysis.reason

        takeout = sum(ratio > 0.18 for ratio in changed_ratios.values()) >= 2
        if takeout:
            self._handle_takeout(camera_ids, "the whole board changed, not a single dart")
            return

        # Less of the board is covered than before this event, so whatever
        # changed was a dart LEAVING, not arriving. Scoring it would invent a
        # throw at the spot a dart was just pulled out of.
        occupancy = self._board_occupancy(post_frames)
        # The exact comparison the takeout decision turns on, recorded whichever
        # way it goes - a trace that only shows the takeouts that fired cannot
        # show a takeout that fired against a stale stored value.
        self._trace_mark(
            "occupancy_test",
            measured=None if occupancy is None else round(occupancy, 5),
            stored=round(self._occupancy, 5),
            drop_threshold=TAKEOUT_OCCUPANCY_DROP,
            would_fire=occupancy is not None and occupancy < self._occupancy - TAKEOUT_OCCUPANCY_DROP,
            changed_ratios={str(cid): round(r, 5) for cid, r in changed_ratios.items()},
        )
        if occupancy is not None and occupancy > MAX_PLAUSIBLE_OCCUPANCY:
            # Too much of the board differs from the reference for it to be
            # darts. Re-anchor and score this throw normally rather than
            # letting a bad reference silently swallow every future one.
            log.warning(
                "board reference looks stale (%.1f%% of the board differs) - re-anchoring",
                occupancy * 100,
            )
            self._reference = {cid: post.copy() for cid, post in post_frames.items()}
            self._occupancy = 0.0
            occupancy = 0.0
        elif occupancy is not None and occupancy < self._occupancy - TAKEOUT_OCCUPANCY_DROP:
            dropped_from = self._occupancy
            for cid, post in post_frames.items():
                self._baselines[cid] = post.copy()
            self._note_occupancy(occupancy)
            self._handle_takeout(
                camera_ids,
                f"board occupancy fell {dropped_from * 100:.2f}% to {occupancy * 100:.2f}%",
            )
            return

        hit = fuse_axes(candidates)
        self._trace_mark("hit", label=hit.label, accepted=hit.accepted, cameras=hit.cameras_used)
        hit_payload = hit.to_dict()
        hit_payload["ts"] = time.time()

        event_id = self._event_seq
        self._event_seq += 1
        hit_payload["event_id"] = event_id
        hit_payload["correction"] = None
        frames_jpeg: dict[int, bytes] = {}
        for cid, post in post_frames.items():
            ok, buf = cv2.imencode(".jpg", post, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                frames_jpeg[cid] = buf.tobytes()
        hit_payload["evidence_cameras"] = sorted(frames_jpeg)
        hit_payload["camera_notes"] = camera_notes

        with self._lock:
            # Same dict object as the history entry, so a correction applied
            # to one is reflected in the other with no extra bookkeeping.
            self.latest_hit = hit_payload
            self.latest_candidates = {c.camera_id: c for c in candidates}
            self._history.appendleft(hit_payload)
            self._evidence[event_id] = frames_jpeg
            self._evidence_order.append(event_id)
            while len(self._evidence_order) > self._evidence_max:
                stale_id = self._evidence_order.popleft()
                self._evidence.pop(stale_id, None)
        self._broadcast({"type": "detection.hit", "hit": hit_payload, "ts": hit_payload["ts"]})
        # Only when a dart was actually located on the board - a "NO HIT"
        # (cameras disagreed, or too few saw it) is not something to
        # celebrate. Fire-and-forget, so an unplugged LED controller can
        # never slow the detection loop down. Detection is blinded for the
        # duration (see _flash_suppressed) because the flash relights the
        # board and would otherwise register as a phantom throw.
        if hit.x_mm is not None:
            led_controller.flash_cue("throw.detected", duration_s=FLASH_DURATION_S)
            self._suppress_until = time.monotonic() + FLASH_SUPPRESS_SECONDS
            self._feed_game(hit)

        for cid, post in post_frames.items():
            self._baselines[cid] = post.copy()
            self._pre_buffers[cid].clear()
            self._pre_buffers[cid].append(post.copy())
            self._cooldown_buffers[cid].clear()
            self._cooldown_last_frames[cid] = post.copy()
        self._note_occupancy(occupancy)
        self._cooldown_stable_since = None
        self._set_state(
            "cooldown",
            f"{hit.label} accepted at {hit.confidence * 100:.0f}%" if hit.accepted else hit.reason,
        )

    def _cooldown(self, frames: dict[int, np.ndarray]) -> None:
        now = time.monotonic()
        with self._lock:
            state_since = self._state_since
        if now - state_since < COOLDOWN_MIN_SECONDS:
            return

        ratios: dict[int, float] = {}
        for cid, frame in frames.items():
            baseline = self._baselines.get(cid)
            if baseline is None or baseline.shape != frame.shape:
                continue
            ratios[cid] = _quick_change_ratio(baseline, frame, self._roi(cid, frame))

        # A genuinely quick next dart must go through the normal trigger
        # path, not get learned into the cooldown background.
        if sum(ratio > MOTION_TRIGGER_RATIO for ratio in ratios.values()) >= 2:
            self._set_state("ready", "New impact detected")
            self._watch_for_event(frames)
            return

        quiet = [cid for cid, ratio in ratios.items() if ratio <= COOLDOWN_QUIET_RATIO]
        for cid in quiet:
            frame = frames[cid]
            previous = self._cooldown_last_frames.get(cid)
            if previous is not None and previous.shape == frame.shape:
                if _quick_change_ratio(previous, frame, self._roi(cid, frame)) > POST_STABLE_RATIO:
                    self._cooldown_buffers[cid].clear()
            self._cooldown_buffers[cid].append(frame.copy())
            self._cooldown_last_frames[cid] = frame.copy()

        if len(quiet) < 2:
            self._cooldown_stable_since = None
            return
        if self._cooldown_stable_since is None:
            self._cooldown_stable_since = now
            return
        if now - self._cooldown_stable_since < COOLDOWN_STABLE_SECONDS:
            return

        for cid in quiet:
            if self._cooldown_buffers[cid]:
                self._baselines[cid] = _median_frame(self._cooldown_buffers[cid])
        self._cooldown_stable_since = None
        self._set_state("ready", "Board stable - waiting for a dart")
