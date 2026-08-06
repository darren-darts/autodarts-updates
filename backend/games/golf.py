"""Darts Golf - the holes are numbers 1 to 18, and low score wins.

Golf is the one game here where scoring is inverted, which is worth knowing
before reading anything else: `player.score` counts *strokes*, so the big
number on every screen goes UP as you play badly and the winner is the
smallest. Everything else in the app treats score as "higher is better", but
nothing enforces that - the scoreboards just show the number - so the only
work needed is sorting the right way round when the round ends.

One hole per turn. The first dart to find the hole scores it and ends the
turn, which is what a real golf hole does: you stop when it's in.
"""
from __future__ import annotations

from .base import Dart, Game, PlayerState, TurnResult
from .registry import register

# Strokes for each outcome, lowest is best.
HOLE_IN_ONE = 1   # treble
BIRDIE = 2        # double
PAR = 3           # single
BOGEY = 5         # never found the hole with any of the three darts

# The strict course. A single no longer gets you par, so the trebles and
# doubles are the only way to keep a decent card - and missing the hole
# entirely hurts more. Deliberately not "outer single only", the real-darts
# version of a strict course: singles reach the engine as multiplier 1 with no
# indication of which band was hit, and the only thing that could tell them
# apart is the dart's x_mm/y_mm, which is None for every hand-entered dart.
# A rule that behaved differently for a detected dart and an overridden one
# would be worse than no rule at all.
STRICT_PAR = 4
STRICT_BOGEY = 6


@register("golf")
class Golf(Game):
    slug = "golf"
    name = "Darts Golf"

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        self.holes = int(self.options.get("holes", 18))
        self.strict = bool(self.options.get("strict", False))
        self.par = STRICT_PAR if self.strict else PAR
        self.bogey = STRICT_BOGEY if self.strict else BOGEY
        # NOT self.round: the engine owns that attribute and increments it
        # every time turn order wraps, so sharing the name would advance the
        # hole twice per round. See ADDING_A_GAME.md, section 2 rule 3.
        self.hole = 1
        for player in self.players:
            player.score = 0
            player.stats = {"holes": [], "pars": 0, "birdies": 0, "aces": 0}
        self._scored_this_turn = False

    # ------------------------------------------------------------ scoring

    def on_turn_start(self, player: PlayerState) -> TurnResult | None:
        self._scored_this_turn = False
        return None

    def _strokes(self, dart: Dart) -> int | None:
        """Strokes for a dart at the current hole, or None if it missed it."""
        if not dart.hits(self.hole):
            return None
        if dart.multiplier == 3:
            return HOLE_IN_ONE
        if dart.multiplier == 2:
            return BIRDIE
        return self.par

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        # on_turn_start does this too, but it is not called when a partially
        # played turn is rebuilt by undo - so the first dart resets it as well.
        if dart_index == 0:
            self._scored_this_turn = False
        if self._scored_this_turn:
            return TurnResult()

        strokes = self._strokes(dart)
        if strokes is None:
            # Missing is silent - the bogey is only decided once all three
            # darts have failed, which is on_turn_end's job.
            return TurnResult()

        self._scored_this_turn = True
        player.score += strokes
        player.stats["holes"].append(strokes)
        if strokes == HOLE_IN_ONE:
            player.stats["aces"] += 1
            return TurnResult(end_turn=True, cue="bullseye", highlight="big",
                              message=f"HOLE IN ONE at the {self.hole}!")
        if strokes == BIRDIE:
            player.stats["birdies"] += 1
            return TurnResult(end_turn=True, highlight="good",
                              message=f"Birdie at the {self.hole} (+{strokes})")
        player.stats["pars"] += 1
        return TurnResult(end_turn=True, highlight="good",
                          message=f"Par at the {self.hole} (+{strokes})")

    def on_turn_end(self, player: PlayerState, darts: list[Dart]) -> TurnResult | None:
        dropped = None
        if not self._scored_this_turn:
            player.score += self.bogey
            player.stats["holes"].append(self.bogey)
            dropped = TurnResult(message=f"Missed the {self.hole} - bogey (+{self.bogey})",
                                 highlight="bad")

        # The hole only moves on once the last player still in has played it.
        active = self.active_players()
        if not active or player is not active[-1]:
            return dropped
        if self.hole >= self.holes:
            return self._finish_round()
        self.hole += 1
        return dropped

    def _finish_round(self) -> TurnResult:
        """Lowest card wins, so places are handed out in ascending score."""
        best = min(self.players, key=lambda p: p.score)
        for p in sorted(self.players, key=lambda p: p.score):
            self.finish_player(p)
        self.finished = True
        self.winner_id = best.player_id
        return TurnResult(finished=True, cue="game.win", highlight="big",
                          message=f"{best.name} wins the round on {best.score} strokes!")

    # ------------------------------------------------------------ presentation

    def target_hint(self, player: PlayerState) -> str | None:
        if self.finished:
            return None
        course = "treble for an ace, double for a birdie" if self.strict else "any part of the number"
        return f"Hole {self.hole} of {self.holes} - {course}"

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        return [] if self.finished else [self.hole]

    def theme(self) -> str:
        return "golf"  # fairway green, sand, and a gold flag on the lit bed

    def view(self) -> dict:
        return {
            "kind": "golf",
            "hole": self.hole,
            "holes": self.holes,
            "strict": self.strict,
            "par": self.par,
            "bogey": self.bogey,
            "cards": {p.player_id: p.stats["holes"] for p in self.players},
        }
