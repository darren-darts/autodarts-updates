"""Autodarts Board Manager adapter.

Turns the local Autodarts detection service into the same scored `Dart`s and
takeout signals the old multi-camera CV pipeline produced, so the existing
game engine is fed identically regardless of where a dart was detected.

    3 USB cameras -> Autodarts -> /api/state -> AutodartsDetector -> Dart -> engine

Autodarts is an *external* service. This module only reads
http://127.0.0.1:3180/api/state and never assumes anything about how Autodarts
does its detection. The rest of InterDarts depends on `games.base.Dart`, not on
Autodarts' JSON shape - that boundary is the whole point, so another detector
could replace this one without rewriting the games.

Three concerns live here, deliberately separable so the tricky parts are pure
and unit-testable without any network or event loop:

  * parse_throw()  - one Autodarts throw entry -> one scored Dart   (pure)
  * VisitTracker   - snapshots -> a clean, de-duplicated event stream (pure)
  * AutodartsDetector - the httpx polling + health + dispatch          (I/O)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from games.base import Dart

log = logging.getLogger("shepdarts.autodarts")

DEFAULT_URL = "http://127.0.0.1:3180/api/state"
POLL_INTERVAL_S = 0.1
HTTP_TIMEOUT_S = 2.0

# Autodarts occasionally wedges reporting a takeout "in progress" and never
# finishes it - usually a lighting/exposure wobble the cameras read as endless
# motion. The board then scores nothing until someone hits Reset. When a takeout
# has been in progress this long, InterDarts fires the same Reset itself to
# unstick it. Comfortably longer than a real takeout (pulling three darts is a
# few seconds) so a slow hand is never cut short.
STUCK_TAKEOUT_S = 8.0
# If a reset doesn't clear it, nudge again on this interval rather than hammering
# /api/reset every poll while the board stays wedged.
AUTO_RESET_COOLDOWN_S = 6.0

# Autodarts reports impact coordinates normalised so the outer double wire (the
# scoring edge) sits at radius 1.0, with x+ right and y+ UP. Board space here is
# millimetres from the bull with x+ right and y+ DOWN (see
# calibration.board_model), so y is negated. The scale and y-sign are pinned by
# a known hit in the Autodarts docs: S20, at 12 o'clock, reports y = +0.76.
SCORING_RADIUS_MM = 170.0


def _label(segment: Optional[int], multiplier: int) -> str:
    """Display label, matching the manual keypad exactly (games.routes._label)
    so a detected dart and a hand-entered one read identically: S/D/T + number,
    BULL for the 50, 25 for the outer bull, MISS for anything off the board."""
    if segment is None or multiplier == 0:
        return "MISS"
    if segment == 25:
        return "BULL" if multiplier == 2 else "25"
    return {1: "S", 2: "D", 3: "T"}.get(multiplier, "S") + str(segment)


def parse_throw(throw: dict, dart_number: int) -> Dart:
    """One Autodarts `throws[]` entry -> one scored `Dart`.

    Score is number*multiplier, which is correct for singles, doubles, trebles
    AND both bulls (25*1 = 25, 25*2 = 50) - so bulls need no special case, only
    the right label. A missing number OR a multiplier of 0 is an OUT: a
    genuinely thrown dart worth nothing (segment None, multiplier 0), not a
    detection failure. Autodarts reports a miss as e.g. {"name":"M6",
    "number":6,"bed":"Outside","multiplier":0} - a nearest-segment number but
    zero multiplier - so the multiplier, not the number, is what says "miss".
    Segment names are NOT parsed: the numeric fields are authoritative and, per
    the Autodarts docs, names don't all start with S/D/T (bull is "25").
    """
    segment_info = throw.get("segment") or {}
    number = segment_info.get("number")
    multiplier = int(segment_info.get("multiplier", 0) or 0)
    coords = throw.get("coords") or {}

    if not number or multiplier == 0:  # off the scoring area / a miss
        segment: Optional[int] = None
        multiplier = 0
        score = 0
    else:
        segment = int(number)
        score = segment * multiplier

    x = coords.get("x")
    y = coords.get("y")
    x_mm = float(x) * SCORING_RADIUS_MM if x is not None else None
    y_mm = -float(y) * SCORING_RADIUS_MM if y is not None else None

    return Dart(
        segment=segment,
        multiplier=multiplier,
        score=score,
        label=_label(segment, multiplier),
        x_mm=x_mm,
        y_mm=y_mm,
    )


# ------------------------------------------------------------------- events


@dataclass
class DartDetected:
    dart: Dart
    dart_number: int


@dataclass
class TakeoutStarted:
    pass


@dataclass
class TakeoutFinished:
    pass


class VisitTracker:
    """Pure state machine over successive /api/state snapshots.

    Autodarts returns its *current state*, not an event stream, and it is polled
    ~10x/second - so the same throw appears in many consecutive snapshots and
    the throw count falls back to 0 as darts are pulled. This collapses that
    into a clean, de-duplicated stream:

      * a DartDetected only when the throw count genuinely *increases*
        (associated with a "Throw detected" event, per the Autodarts docs)
      * TakeoutStarted / TakeoutFinished exactly once per visit
      * nothing at all for a repeated identical snapshot or a falling count

    Startup guard: Autodarts keeps its last event across restarts, so the first
    snapshot InterDarts sees can already read "Takeout finished". Takeout events
    are ignored until an active visit (an actual throw) has been observed during
    the current lifecycle - so a stale event never fires a phantom "visit over".
    """

    def __init__(self) -> None:
        self._last_count = 0
        self._visit_active = False
        self._takeout_active = False
        self._seen_active_visit = False

    def update(self, state: dict) -> list:
        events: list = []
        throws = state.get("throws") or []
        event = state.get("event")
        # len(throws) is authoritative for *what* was thrown (it carries the
        # segments); numThrows only agrees and can't be parsed on its own. Fall
        # back to numThrows only when the array is absent.
        count = len(throws) if throws else int(state.get("numThrows") or 0)

        if count > self._last_count:
            if event == "Throw detected":
                for i in range(self._last_count, count):
                    if i < len(throws):
                        events.append(DartDetected(parse_throw(throws[i], i + 1), i + 1))
                    else:
                        log.warning(
                            "Autodarts count=%s but only %s throws present - skipping",
                            count, len(throws),
                        )
                self._visit_active = True
                self._seen_active_visit = True
            else:
                # Count grew without a throw event (e.g. a stale array lingering
                # after a takeout). Adopt the count so it can never be re-read as
                # new darts, but emit nothing.
                log.debug("count rose to %s with event=%r - not emitting", count, event)
            self._last_count = count
        elif count < self._last_count:
            # Darts being pulled: 3 -> 2 -> 1 -> 0. Never a new dart.
            self._last_count = count

        if event == "Takeout started" and not self._takeout_active:
            if self._seen_active_visit and self._visit_active:
                events.append(TakeoutStarted())
                self._takeout_active = True

        if event == "Takeout finished":
            if self._seen_active_visit and (self._takeout_active or self._visit_active):
                events.append(TakeoutFinished())
                self._reset_visit()

        return events

    def _reset_visit(self) -> None:
        self._last_count = 0
        self._visit_active = False
        self._takeout_active = False


# --------------------------------------------------------------- the detector


def _safe(fn: Optional[Callable], *args) -> None:
    """A handler bug (a game rejecting a dart, say) must never kill the poll
    loop - the detector has to keep running so the board keeps scoring."""
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:
        log.exception("Autodarts event handler failed - detection continues")


class AutodartsDetector:
    """Polls /api/state and dispatches normalised events to callbacks.

    Decoupled from the games by design: it is handed plain callbacks (typically
    match_engine.submit_dart and next_turn) rather than importing the engine, so
    the same detector could feed a queue, a test double, or a different engine.
    """

    def __init__(
        self,
        on_dart: Callable[[Dart], None],
        on_takeout_finished: Callable[[], None],
        on_takeout_started: Optional[Callable[[], None]] = None,
        url: str = DEFAULT_URL,
        poll_interval: float = POLL_INTERVAL_S,
        stuck_takeout_s: float = STUCK_TAKEOUT_S,
        auto_recover: bool = True,
    ) -> None:
        self._on_dart = on_dart
        self._on_takeout_finished = on_takeout_finished
        self._on_takeout_started = on_takeout_started
        self._url = url
        self._poll_interval = poll_interval
        self._tracker = VisitTracker()
        self._task: Optional[asyncio.Task] = None
        self._client = None  # httpx.AsyncClient, created in start()

        # Stuck-takeout auto-recovery.
        self._auto_recover = auto_recover
        self._stuck_takeout_s = stuck_takeout_s
        self._takeout_since: Optional[float] = None   # monotonic, when takeout began
        self._last_auto_reset = 0.0                   # monotonic, last auto-reset

        # Health, surfaced at /api/detection/autodarts.
        self.available = False   # did the last request succeed?
        self.connected = False   # Autodarts reports cameras connected
        self.running = False     # Autodarts reports its board running
        self.stuck = False       # takeout wedged; we are auto-resetting to recover
        self.auto_resets = 0     # how many auto-resets fired this lifetime
        self._last_error: Optional[str] = None
        self._offline_logged = False

    async def start(self) -> None:
        if self._task is not None:
            return
        import httpx  # lazy: keeps the module importable (and testable) without httpx

        self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
        self._task = asyncio.create_task(self._poll_loop(), name="autodarts-poll")
        log.info(
            "Autodarts detector polling %s every %dms",
            self._url, int(self._poll_interval * 1000),
        )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        log.info("Autodarts detector stopped")

    def status(self) -> dict:
        return {
            "detector": "autodarts",
            "url": self._url,
            "available": self.available,
            "connected": self.connected,
            "running": self.running,
            "stuck": self.stuck,
            "auto_resets": self.auto_resets,
            "error": self._last_error,
        }

    def _reset_url(self) -> str:
        # The board-manager API base is shared: /api/state -> /api/reset. Same
        # command the Board Manager's own Reset button fires.
        base = self._url[: -len("/state")] if self._url.endswith("/state") else self._url.rsplit("/", 1)[0]
        return base + "/reset"

    async def reset(self) -> dict:
        """Reset Autodarts' current throw detection - the "Manual reset" its own
        Board Manager button fires (POST /api/reset). Clears the darts it
        currently sees so a mis-detected visit can be thrown again. Raises on a
        transport error so the caller can report it; the poll loop is unaffected
        and simply sees the count drop to zero on the next tick.
        """
        import httpx  # lazy, mirrors start(): module stays importable without httpx

        if self._client is None:
            raise RuntimeError("Autodarts detector is not running")
        resp = await self._client.post(self._reset_url())
        resp.raise_for_status()
        log.info("Autodarts board reset (manual)")
        return {"reset": True}

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # a transient failure must never end the loop
                log.exception("Autodarts poll error - continuing")
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        import httpx

        try:
            resp = await self._client.get(self._url)
            resp.raise_for_status()
            state = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._mark_offline(f"{type(exc).__name__}: {exc}")
            return
        if not isinstance(state, dict):
            self._mark_offline("unexpected /api/state payload (not an object)")
            return
        self._mark_online(state)
        for event in self._tracker.update(state):
            self._dispatch(event)
        await self._auto_recover_takeout(state)

    async def _auto_recover_takeout(self, state: dict) -> None:
        """Nudge Autodarts out of a takeout it has got wedged in.

        Takeout events here are read straight from the raw state, not the
        filtered VisitTracker stream, because a board can be stuck in takeout
        with no InterDarts visit in play at all (e.g. left wedged from a previous
        session). While `event` stays "Takeout started" past the threshold, fire
        the same Reset the Board Manager's button does, rate-limited by a cooldown
        so a genuinely broken board is nudged occasionally rather than hammered.
        """
        if not self._auto_recover:
            return
        now = time.monotonic()
        if state.get("event") != "Takeout started":
            self._takeout_since = None
            self.stuck = False
            return
        if self._takeout_since is None:
            self._takeout_since = now
            return
        if now - self._takeout_since < self._stuck_takeout_s:
            return
        self.stuck = True
        if now - self._last_auto_reset < AUTO_RESET_COOLDOWN_S:
            return
        self._last_auto_reset = now
        try:
            await self.reset()
            self.auto_resets += 1
            log.warning(
                "Autodarts wedged in takeout for %.0fs - auto-reset #%d fired",
                now - self._takeout_since, self.auto_resets,
            )
        except Exception as exc:
            log.warning("Autodarts auto-reset failed: %s", exc)

    def _dispatch(self, event) -> None:
        if isinstance(event, DartDetected):
            log.info("Dart detected: %s = %s", event.dart.label, event.dart.score)
            _safe(self._on_dart, event.dart)
        elif isinstance(event, TakeoutStarted):
            log.info("Takeout started")
            _safe(self._on_takeout_started)
        elif isinstance(event, TakeoutFinished):
            log.info("Takeout finished")
            _safe(self._on_takeout_finished)

    def _mark_online(self, state: dict) -> None:
        if self._offline_logged:
            log.info("Autodarts detector connected")
            self._offline_logged = False
        self.available = True
        self.connected = bool(state.get("connected"))
        self.running = bool(state.get("running"))
        self._last_error = None

    def _mark_offline(self, err: str) -> None:
        if not self._offline_logged:
            log.warning(
                "Autodarts detection service unavailable (%s) - retrying. "
                "Start or configure Autodarts before playing.", err,
            )
            self._offline_logged = True
        self.available = False
        self.connected = False
        self.running = False
        # An unreachable board can't be stuck-in-takeout from our side; clear the
        # recovery state so it starts fresh when it comes back.
        self.stuck = False
        self._takeout_since = None
        self._last_error = err
