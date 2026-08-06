"""The contract every dart game implements.

One shared core drives all games: darts arrive from the detection pipeline,
players come from the roster, and the engine handles whose turn it is and
when a turn ends. A game only decides what a dart *means*.

Adding a game means subclassing Game and implementing apply_dart() plus
view(); everything else - turn rotation, dart counting, undo, the live UI
feed, LED and sound cues - comes for free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Dart:
    """One scored dart, as handed over by the detection pipeline."""
    segment: int | None       # 1-20, 25 for bull, None if off-board
    multiplier: int           # 0 (miss/out), 1, 2 (double), 3 (treble)
    score: int                # segment * multiplier, already resolved
    label: str                # "T20", "D16", "BULL", "25", "OUT"
    x_mm: float | None = None
    y_mm: float | None = None

    @property
    def is_bull(self) -> bool:
        return self.segment == 25 and self.multiplier == 2

    @property
    def is_outer_bull(self) -> bool:
        return self.segment == 25 and self.multiplier == 1

    @property
    def is_miss(self) -> bool:
        return self.multiplier == 0 or self.segment is None

    def hits(self, number: int) -> bool:
        """True if this dart landed in the given number's bed, any ring."""
        return self.segment == number and self.multiplier > 0


@dataclass
class TurnResult:
    """What the game wants to happen after a dart.

    Games set these rather than driving the engine directly, so turn
    handling stays in one place.
    """
    end_turn: bool = False        # stop the turn early (bust, life lost, ...)
    message: str | None = None    # short line for the player, e.g. "BUST!"
    cue: str | None = None        # LED cue name to fire
    finished: bool = False        # the game is over
    highlight: str | None = None  # "good" | "bad" | "big" for UI/sound emphasis


@dataclass
class PlayerState:
    """Per-player bookkeeping shared by every game."""
    player_id: str
    name: str
    avatar: str | None = None
    score: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    finished: bool = False
    place: int | None = None      # 1 = winner, set as players finish

    def view(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "avatar": self.avatar,
            "score": self.score,
            "stats": self.stats,
            "finished": self.finished,
            "place": self.place,
        }


class Game:
    """Base class for every game.

    Subclasses set the metadata class attributes and implement apply_dart()
    and view(). See registry.py for how a game is advertised to the UI.
    """

    slug: str = ""
    name: str = ""
    darts_per_turn: int = 3

    def __init__(self, players: list[PlayerState], difficulty: str, options: dict | None = None):
        self.players = players
        self.difficulty = difficulty
        self.options = options or {}
        self.finished = False
        self.winner_id: str | None = None
        self.round = 1

    # ------------------------------------------------------------ hooks

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        """Score one dart for the given player. dart_index is 0-based within
        the turn. Must be implemented by every game."""
        raise NotImplementedError

    def on_turn_start(self, player: PlayerState) -> TurnResult | None:
        """Optional: called before a player's first dart (target selection,
        per-round setup, ...)."""
        return None

    def on_turn_end(self, player: PlayerState, darts: list[Dart]) -> TurnResult | None:
        """Optional: called once the turn's darts are done."""
        return None

    def target_hint(self, player: PlayerState) -> str | None:
        """What the player should be aiming at right now, shown large on the
        play screen. None if the game has no single target."""
        return None

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        """Board numbers to light up on the live play board (25 = bull).

        This is what makes the big dartboard *useful* rather than
        decorative: the beds you need are picked out on the board itself,
        so nobody has to translate a text hint while standing at the oche.
        """
        return []

    def theme(self) -> str:
        """Palette for the live board: classic | killer | space | derby."""
        return "classic"

    def view(self) -> dict[str, Any]:
        """Game-specific state for the UI (board overlays, tracks, grids...)."""
        return {}

    # ------------------------------------------------------------ helpers

    def active_players(self) -> list[PlayerState]:
        return [p for p in self.players if not p.finished]

    def eliminate(self, player: PlayerState) -> None:
        """Knock a player OUT (Killer, Nine Lives, Sudden Death...).

        The opposite of finish_player: there, finishing first means winning,
        so places count up from 1. Here the first player out comes LAST, so
        places are filled from the bottom and the survivor takes first.
        """
        if player.finished:
            return
        player.finished = True
        player.place = len(self.active_players()) + 1
        survivors = self.active_players()
        if len(survivors) <= 1:
            self.finished = True
            for survivor in survivors:
                survivor.finished = True
                survivor.place = 1
                self.winner_id = survivor.player_id

    def finish_player(self, player: PlayerState) -> None:
        """Mark a player as done, assigning the next finishing place."""
        if player.finished:
            return
        player.finished = True
        taken = [p.place for p in self.players if p.place is not None]
        player.place = (max(taken) + 1) if taken else 1
        if player.place == 1:
            self.winner_id = player.player_id
        if len(self.active_players()) <= 1:
            self.finished = True
            for remaining in self.active_players():
                remaining.finished = True
                taken = [p.place for p in self.players if p.place is not None]
                remaining.place = (max(taken) + 1) if taken else 1
