"""Runs one match: whose turn it is, how many darts they've thrown, and
feeding scored darts from the detection pipeline into the active game.

The engine owns everything common to all games so individual games stay
small. It also owns the outward-facing effects (WebSocket broadcast, LED
cues, and the takeout/next-player flow) so a game never has to know they
exist.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from events import hub
from leds.controller import led_controller
from players import store as player_store

from .base import Dart, Game, PlayerState, TurnResult
from .registry import build_game, get_definition

log = logging.getLogger(__name__)

# Every game event is a *momentary* effect that returns the board to bright
# white. No firmware effect self-terminates, so anything fired with cue()
# would stay on the board indefinitely - which is both visually wrong and
# actively harmful: the cameras need flat bright white to score reliably,
# and a slow colour wash is exactly what the detector reads as motion.
# Durations are tuned to how big a moment each event is.
CUE_SECONDS = {
    "game.start": 2.0,
    "turn.start": 0.7,
    "throw.detected": 0.5,
    "bullseye": 1.5,
    "score.180": 2.5,
    "bust": 1.4,
    "game.win": 5.0,
}
DEFAULT_CUE_SECONDS = 0.8


def _flash(name: str) -> None:
    led_controller.flash_cue(name, duration_s=CUE_SECONDS.get(name, DEFAULT_CUE_SECONDS))


class MatchEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.game: Game | None = None
        self.slug: str | None = None
        self.difficulty: str | None = None
        self.turn_index = 0            # index into game.players
        self.darts_this_turn: list[Dart] = []
        self.message: str | None = None
        self.history: list[dict] = []  # recent scored darts, newest first
        self.awaiting_takeout = False
        # Every action in order, so undo can replay the match exactly.
        self._log: list[tuple] = []
        # Length of _log at the moment the "remove the darts" prompt appeared.
        # Detection keeps watching during that prompt and can get it wrong -
        # firing early on a hand reaching in, or twice, which skips a player.
        # Confirming the takeout by hand rewinds to here first, so the button
        # is the authority over anything detection did in the meantime.
        self._takeout_checkpoint: int | None = None

    # ------------------------------------------------------------ lifecycle

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def start(self, slug: str, difficulty: str, player_ids: list[str] | None = None,
              options: dict | None = None) -> dict:
        definition = get_definition(slug)
        if definition is None:
            raise ValueError(f"unknown game {slug!r}")
        if difficulty not in definition["difficulties"]:
            raise ValueError(f"unknown difficulty {difficulty!r} for {slug}")

        roster = player_store.list_players()
        if player_ids:
            chosen = [p for p in roster if p["id"] in player_ids]
        else:
            chosen = roster
        if not chosen:
            raise ValueError("no players selected")
        if len(chosen) < definition["min_players"]:
            raise ValueError(f"{definition['name']} needs at least {definition['min_players']} players")

        states = [
            PlayerState(player_id=p["id"], name=p.get("name") or "Player", avatar=p.get("avatar"))
            for p in chosen
        ]
        with self._lock:
            self.game = build_game(slug, states, difficulty, options)
            self.slug = slug
            self.difficulty = difficulty
            self.turn_index = 0
            self.darts_this_turn = []
            self.history = []
            self.awaiting_takeout = False
            self.message = None
            self._log = []
            opener = self.game.on_turn_start(self.current_player())
            if opener and opener.message:
                self.message = opener.message
        # Guarantee the board is back to flat bright white for the whole
        # match: whatever the LED page or a previous game left behind, the
        # cameras get the lighting they were calibrated under, and every
        # game effect from here returns to it.
        led_controller.set_resting_cue("startup")
        _flash("game.start")
        self._broadcast()
        return self.state()

    def stop(self) -> None:
        with self._lock:
            self.game = None
            self.slug = None
            self.difficulty = None
            self.darts_this_turn = []
            self.message = None
        # Leave the board on plain bright white rather than mid-celebration.
        led_controller.set_resting_cue("startup")
        self._broadcast()

    # ------------------------------------------------------------ play

    def current_player(self) -> PlayerState:
        assert self.game is not None
        players = self.game.players
        return players[self.turn_index % len(players)]

    def submit_dart(self, dart: Dart) -> dict:
        """Score one dart against the active game. Safe to call from the
        detection thread."""
        cue = None
        with self._lock:
            if self.game is None or self.game.finished:
                return self.state()
            if self.awaiting_takeout:
                # The turn's darts are all thrown. Discarding silently is the
                # worst option: a manually added dart, or one detected while
                # someone reaches for the board, just vanishes and looks like
                # the app ignored it. Say so instead.
                self.message = "Turn over - remove the darts before the next player throws."
                self._broadcast()
                return self.state()
            self._log.append(("dart", dart))
            cue = self._apply_dart_locked(dart)
        if cue:
            _flash(cue)
        self._broadcast()
        return self.state()

    def _apply_dart_locked(self, dart: Dart) -> str | None:
        assert self.game is not None
        if self.game.finished or self.awaiting_takeout:
            return None
        # A dart being scored means play has genuinely resumed, so whatever
        # happened around the last takeout is now settled and no longer
        # rewindable. Set again below if this dart ends the turn.
        self._takeout_checkpoint = None
        player = self.current_player()
        result = self.game.apply_dart(player, dart, len(self.darts_this_turn))
        self.darts_this_turn.append(dart)
        self.history.insert(0, {
            "player": player.name, "label": dart.label, "score": dart.score,
            "message": result.message, "highlight": result.highlight,
        })
        del self.history[40:]
        if result.message:
            self.message = result.message
        turn_over = (
            result.end_turn
            or len(self.darts_this_turn) >= self.game.darts_per_turn
        )
        if self.game.finished or result.finished:
            self.game.finished = True
            return "game.win"
        if turn_over:
            closing = self.game.on_turn_end(player, list(self.darts_this_turn))
            if closing and closing.message:
                self.message = closing.message
            if self.game.finished:
                return "game.win"
            # Darts stay in the board until pulled, so the next player can't
            # start yet - the detection takeout advances the turn.
            self.awaiting_takeout = True
            self._takeout_checkpoint = len(self._log)
        # A game's own cue wins; otherwise a bullseye is the one dart worth
        # celebrating on its own. (This used to fire the 180 celebration for
        # any 60-point dart, i.e. every single treble 20.)
        return result.cue or ("bullseye" if dart.is_bull else None)

    def next_turn(self, cue: bool = True) -> dict:
        """Advance to the next player. Called when the board is cleared
        (detection reports a takeout) or the user presses Next.

        `cue=False` when detection spotted a takeout: that path fires its own
        red flash, and firing turn.start's blue comet straight afterwards
        would overwrite it a few milliseconds in. One event, one cue.
        """
        with self._lock:
            if self.game is None or self.game.finished:
                return self.state()
            self._log.append(("next",))
            self._advance_turn_locked()
        if cue:
            _flash("turn.start")
        self._broadcast()
        return self.state()

    def _advance_turn_locked(self) -> None:
        assert self.game is not None
        self.awaiting_takeout = False
        self.darts_this_turn = []
        players = self.game.players
        for _ in range(len(players)):
            self.turn_index = (self.turn_index + 1) % len(players)
            if not players[self.turn_index].finished:
                break
        if self.turn_index == 0:
            self.game.round += 1
        opener = self.game.on_turn_start(self.current_player())
        self.message = opener.message if opener and opener.message else None

    def confirm_takeout(self) -> dict:
        """The human says the darts are out. This is the authority.

        Detection keeps watching all the way through the "remove the darts"
        prompt, which is what you want - most of the time it gets it right and
        nobody has to touch anything. When it doesn't, it fails in two ways
        that both look like the app losing track: it fires early on a hand
        reaching in (so the next player is up while darts are still stuck in
        the board), or it fires twice (skipping a player entirely).

        So this rewinds the match to the moment the prompt appeared, throwing
        away whatever happened since, and then advances exactly one turn. If
        detection got it right, the rewind-and-advance lands on precisely the
        same state, which makes the button safe to press either way.
        """
        with self._lock:
            if self.game is None or self.game.finished:
                return self.state()
            checkpoint = self._takeout_checkpoint
            if checkpoint is not None and len(self._log) > checkpoint:
                log.info(
                    "takeout confirmed by hand - discarding %d action(s) detection recorded "
                    "since the prompt appeared",
                    len(self._log) - checkpoint,
                )
                self._replay_locked(self._log[:checkpoint])
            self._log.append(("next",))
            self._advance_turn_locked()
            self._takeout_checkpoint = None
        _flash("turn.start")
        self._broadcast()
        return self.state()

    def undo_dart(self) -> dict:
        """Undo the last dart by replaying the whole match without it.

        Games are deterministic, so replaying an action log is exact and -
        unlike asking each game to implement an inverse of its own scoring -
        cannot drift out of sync as games are added.
        """
        with self._lock:
            if self.game is None:
                return self.state()
            last_dart = next(
                (i for i in range(len(self._log) - 1, -1, -1) if self._log[i][0] == "dart"),
                None,
            )
            if last_dart is None:
                return self.state()
            log = self._log[:last_dart] + self._log[last_dart + 1:]
            self._replay_locked(log)
        self._broadcast()
        return self.state()

    def previous_turn(self) -> dict:
        """Go back to the player who was up before the last turn change.

        Rewinds to the moment *before* the turn advanced and discards anything
        recorded since, rather than just stepping the player pointer back. The
        pointer-only version cannot express what this is for: when a takeout
        fires twice, or fires early, the wrong player has been up and whatever
        landed since belongs to nobody. Truncating is also the only version
        that restores the previous player's own darts, so they resume exactly
        where they were rather than starting a fresh turn.

        Same replay-the-log mechanism as undo_dart, for the same reason: games
        are deterministic, so rebuilding is exact and cannot drift as new games
        are added.
        """
        with self._lock:
            if self.game is None:
                return self.state()
            last_advance = next(
                (i for i in range(len(self._log) - 1, -1, -1) if self._log[i][0] == "next"),
                None,
            )
            if last_advance is None:
                self.message = "Already on the first turn."
                self._broadcast()
                return self.state()
            discarded = sum(1 for action in self._log[last_advance + 1:] if action[0] == "dart")
            self._replay_locked(self._log[:last_advance])
            player = self.current_player()
            self.message = (
                f"Back to {player.name}"
                + (f" - {discarded} dart{'s' if discarded != 1 else ''} discarded" if discarded else "")
            )
        self._broadcast()
        return self.state()

    def _replay_locked(self, log: list[tuple]) -> None:
        """Rebuild the match from scratch and re-apply an action log.

        The replayed actions are pushed back onto `_log` as they are applied.
        Leaving it empty (as this used to) meant the very first undo threw the
        whole history away, so a second undo found nothing to remove and
        silently did nothing - and any later rewind had no log to rewind to.
        """
        assert self.game is not None
        seed = [
            PlayerState(player_id=p.player_id, name=p.name, avatar=p.avatar)
            for p in self.game.players
        ]
        self.game = build_game(self.slug, seed, self.difficulty, self.game.options)
        self.turn_index = 0
        self.darts_this_turn = []
        self.history = []
        self.awaiting_takeout = False
        self.message = None
        self._takeout_checkpoint = None
        self._log = []
        self.game.on_turn_start(self.current_player())
        for action in log:
            self._log.append(action)
            if action[0] == "dart":
                self._apply_dart_locked(action[1])
            else:
                self._advance_turn_locked()

    # ------------------------------------------------------------ state

    def state(self) -> dict[str, Any]:
        with self._lock:
            if self.game is None:
                return {"active": False}
            definition = get_definition(self.slug) or {}
            player = self.current_player()
            return {
                "active": True,
                "slug": self.slug,
                "name": definition.get("name", self.slug),
                "difficulty": self.difficulty,
                "round": self.game.round,
                "finished": self.game.finished,
                "winner_id": self.game.winner_id,
                "current_player_id": player.player_id,
                # x/y come straight from the detector when a real dart was
                # thrown, so the live board can plot the actual landing spot
                # rather than the middle of the bed. Null for manual entry.
                "darts_this_turn": [
                    {"label": d.label, "score": d.score, "x_mm": d.x_mm, "y_mm": d.y_mm}
                    for d in self.darts_this_turn
                ],
                "theme": self.game.theme(),
                "highlight": self.game.highlight_numbers(player),
                "darts_per_turn": self.game.darts_per_turn,
                "awaiting_takeout": self.awaiting_takeout,
                # True while a hand-confirmed takeout would still override
                # whatever detection did - i.e. the prompt should stay up even
                # if detection has already advanced the turn.
                "takeout_override": self._takeout_checkpoint is not None,
                "message": self.message,
                "target_hint": self.game.target_hint(player),
                "players": [p.view() for p in self.game.players],
                "game": self.game.view(),
                "history": self.history[:12],
            }

    def _broadcast(self) -> None:
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                hub.broadcast({"type": "game.state", "state": self.state()}), self._loop
            )
        except RuntimeError:
            pass


match_engine = MatchEngine()
