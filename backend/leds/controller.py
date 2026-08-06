"""LedController — the one LED hook for the whole application.

Calibration, games, and API routes all use this via the module-level
`led_controller` singleton:

    from leds.controller import led_controller
    led_controller.cue("calibration.start")          # named cue from settings
    led_controller.send({"fx": "CELEBRATION"})       # raw state, fx by name ok

Design rules:
- Fire-and-forget: sends go through a background worker thread, so a
  missing/unplugged LED controller can never stall a game or calibration.
- Transport (USB serial vs WiFi HTTP) is chosen from settings; "auto"
  prefers a detected USB serial port and falls back to HTTP.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

import settings_store
from leds.effects import resolve_fx
from leds.transport import (
    HttpTransport,
    SerialTransport,
    TransportError,
    guess_esp32_port,
)

log = logging.getLogger(__name__)

RECONNECT_BACKOFF_S = 3.0
# Every LED state change relights the dartboard, and a frame-difference
# detector cannot tell a lighting change from a dart. Detection reads
# `lighting_busy_until` and drops frames while it holds.
#
# This lives here rather than at the detector's call sites because that is
# exactly what went wrong: the detector blinded itself around its OWN two
# flashes, so any cue fired from anywhere else reached the cameras with
# detection wide open. Crowning a Killer fires the "bullseye" cue, and the
# relight registered as a board-wide change - i.e. as the darts being removed.
# Anything that reaches send() now blinds detection, with no wiring per caller.
LIGHTING_SETTLE_SECONDS = 0.35


class LedController:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue(maxsize=64)
        self._transport = None
        self._lock = threading.Lock()  # guards _transport
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_connect_fail = 0.0
        self.last_error: str | None = None
        self.last_sent: dict | None = None
        # The state transient cues return to. The firmware holds whatever
        # state it was last given - no effect self-terminates - so anything
        # momentary (a dart-detected flash) only looks momentary because we
        # send the resting state back afterwards.
        #
        # Deliberately NOT guarded by _lock: that one is held across blocking
        # transport I/O (a serial write can take up to its 2s timeout), and
        # flash_cue is called from the detection thread, which must never
        # stall waiting on the LED link.
        self._flash_lock = threading.Lock()
        self._resting: dict | None = None
        self._revert_timer: threading.Timer | None = None
        # Monotonic deadline until which the board is being relit - see
        # LIGHTING_SETTLE_SECONDS. Shares _flash_lock rather than _lock for the
        # same reason _resting does: detection reads this every frame and must
        # never block behind transport I/O.
        self._lighting_busy_until = 0.0

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="leds", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._cancel_revert()
        self._queue.put(None)  # wake the worker
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._close_transport()

    def reconfigure(self) -> None:
        """Call after LED settings change; drops the connection so the next
        send uses the new transport config."""
        self._close_transport()

    # ------------------------------------------------------------ public API

    def send(self, state: dict) -> None:
        """Queue a partial LED state update (same schema as the firmware:
        on/bri/fx/sx/col/col2/skip/len). fx may be an effect name."""
        payload = dict(state)
        if "fx" in payload:
            try:
                payload["fx"] = resolve_fx(payload["fx"])
            except ValueError as e:
                log.warning("led send dropped: %s", e)
                return
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            log.warning("led queue full, dropping update")
            return
        # Only once the update is genuinely on its way - blinding detection for
        # a send that was dropped or had an unresolvable effect would be pure
        # lost sensitivity.
        self._note_lighting_busy(0.0)

    def _note_lighting_busy(self, seconds: float) -> None:
        """Declare the board relit for `seconds`, plus a settling margin."""
        until = time.monotonic() + seconds + LIGHTING_SETTLE_SECONDS
        with self._flash_lock:
            self._lighting_busy_until = max(self._lighting_busy_until, until)

    @property
    def lighting_busy_until(self) -> float:
        """Monotonic time until which frames are unsafe to compare. 0.0 when
        no cue has ever fired, which reads as "not busy" against any real
        monotonic clock value."""
        with self._flash_lock:
            return self._lighting_busy_until

    def cue(self, name: str) -> bool:
        """Fire a named cue from settings (e.g. "calibration.start").
        Returns False if the cue isn't defined."""
        state = self._cue_state(name)
        if state is None:
            return False
        # A lasting cue supersedes any flash still counting down, otherwise
        # the pending revert would undo it a moment later.
        self._cancel_revert()
        self.send(state)
        return True

    def set_resting_cue(self, name: str) -> bool:
        """Fire a cue and make it the state transient flashes return to."""
        state = self._cue_state(name)
        if state is None:
            return False
        with self._flash_lock:
            self._resting = dict(state)
        return self.cue(name)

    def flash_cue(self, name: str, duration_s: float = 1.0) -> bool:
        """Fire a cue, then return to the resting state after duration_s.

        Repeat calls restart the countdown rather than stacking, so darts
        thrown in quick succession hold the flash until a second after the
        last one instead of reverting mid-sequence."""
        state = self._cue_state(name)
        if state is None:
            return False
        with self._flash_lock:
            resting = dict(self._resting) if self._resting else None
        self._cancel_revert()
        self.send(state)
        # send() only covers its own settle, but the board holds this cue's
        # colour for the whole of duration_s before reverting - and cues run up
        # to 5s (game.win). Without the full span, detection would re-open
        # mid-cue and see the changed lighting as a board-wide event.
        self._note_lighting_busy(duration_s)
        if resting is None:
            return True  # nothing to revert to; the cue just persists
        timer = threading.Timer(duration_s, self.send, args=(resting,))
        timer.daemon = True
        with self._flash_lock:
            self._revert_timer = timer
        timer.start()
        return True

    def request(self, cmd: str) -> dict:
        """Synchronous query (state/effects/info/ping) for API routes.
        Raises TransportError if the controller is unreachable."""
        with self._lock:
            transport = self._ensure_transport_locked()
            if transport is None:
                raise TransportError(self.last_error or "LED controller not configured")
            try:
                return transport.send({"cmd": cmd})
            except TransportError as e:
                self.last_error = str(e)
                self._close_transport_locked()
                raise

    def status(self) -> dict:
        settings = settings_store.load()["leds"]
        with self._lock:
            connected = self._transport is not None
            return {
                "transport_setting": settings["transport"],
                "connected": connected,
                "active_transport": self._transport.kind if connected else None,
                "target": self._transport.target if connected else None,
                "last_error": self.last_error,
                "last_sent": self.last_sent,
            }

    # ------------------------------------------------------------ internals

    def _cue_state(self, name: str) -> dict | None:
        state = settings_store.load()["leds"]["cues"].get(name)
        if state is None:
            log.warning("unknown led cue %r", name)
        return state

    def _cancel_revert(self) -> None:
        with self._flash_lock:
            timer, self._revert_timer = self._revert_timer, None
        if timer is not None:
            timer.cancel()

    def _run(self) -> None:
        while not self._stop.is_set():
            payload = self._queue.get()
            if payload is None or self._stop.is_set():
                break
            with self._lock:
                transport = self._ensure_transport_locked()
                if transport is None:
                    continue  # fire-and-forget: drop while unreachable
                try:
                    transport.send(payload)
                    self.last_sent = payload
                    self.last_error = None
                except TransportError as e:
                    self.last_error = str(e)
                    log.warning("led send failed: %s", e)
                    self._close_transport_locked()

    def _ensure_transport_locked(self):
        if self._transport is not None:
            return self._transport
        if time.monotonic() - self._last_connect_fail < RECONNECT_BACKOFF_S:
            return None

        settings = settings_store.load()["leds"]
        mode = settings["transport"]
        if mode == "off":
            self.last_error = None
            return None

        try:
            if mode in ("auto", "serial"):
                port = settings.get("serial_port") or guess_esp32_port()
                if port:
                    try:
                        self._transport = SerialTransport(port)
                        log.info("led transport: serial on %s", port)
                        return self._transport
                    except TransportError as e:
                        if mode == "serial":
                            raise
                        log.info("serial unavailable (%s), trying http", e)
                elif mode == "serial":
                    raise TransportError("no serial port configured or detected")
            # http, or auto falling through
            self._transport = HttpTransport(settings["http_url"])
            self._transport.send({"cmd": "ping"})  # verify reachability now
            log.info("led transport: http to %s", settings["http_url"])
            return self._transport
        except TransportError as e:
            self.last_error = str(e)
            self._last_connect_fail = time.monotonic()
            self._close_transport_locked()
            return None

    def _close_transport_locked(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _close_transport(self) -> None:
        with self._lock:
            self._close_transport_locked()


led_controller = LedController()
