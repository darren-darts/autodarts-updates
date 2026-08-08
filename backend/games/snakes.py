"""Snakes & Ladders - every dart moves, and reacts, on its own.

A darts twist on the board game, played one dart at a time. Each of a player's
three darts is resolved the moment it lands: its score becomes a number of
squares, the token moves that far, and any ladder or snake under the landing
square is taken *immediately* - so the next dart is thrown from wherever the
token ended up, ladder-climb and snake-slide included. That "throw, move, react"
rhythm, rather than "throw three then move once", is the whole point of the
rework.

The board is the classic 10x10 boustrophedon: square 1 at the bottom-left,
numbering left-to-right then back right-to-left row by row, up to 100 at the
top-left. The snake and ladder positions are read off the reference artwork and
kept in one place so the board can be re-tuned without touching the rules.

Nothing in here is random, so the engine's replay-based undo (ADDING_A_GAME.md
section 2) rebuilds an identical game every time.
"""
from __future__ import annotations

from .base import Dart, Game, PlayerState, TurnResult
from .registry import register

FINISH = 100
COLUMNS = 10

# Ladders climb (foot -> top); snakes slide (head -> tail). Read off the
# reference board. Every square appears in at most one of the two, none sit on
# square 1 or 100, and each pairing runs the right way (ladders up, snakes down).
LADDERS = {15: 37, 18: 84, 53: 74, 71: 91}
SNAKES = {59: 48, 64: 36, 87: 28, 98: 17}

DEFAULT_DIVISOR = 5


def movement_for(dart_score: int, divisor: int = DEFAULT_DIVISOR) -> int:
    """Turn a dart's score into a number of board squares.

    Kept separate and configurable so game speed can be tuned without touching
    the rules: spaces = ceil(score / divisor). Moving by the raw score would end
    the game in two or three turns, so it is scaled down. `-(-a // b)` is ceiling
    division on integers.
    """
    if dart_score <= 0 or divisor <= 0:
        return 0
    return -(-dart_score // divisor)


@register("snakes-and-ladders")
class SnakesAndLadders(Game):
    slug = "snakes-and-ladders"
    name = "Snakes & Ladders"

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        # The divisor turns a dart score into squares (see movement_for). Read
        # from options so the difficulty levels can set their own pace.
        self.divisor = int(self.options.get("divisor", DEFAULT_DIVISOR))
        # Monotonic id bumped on every dart. The board watches this to know a new
        # dart has been resolved and animates the move it describes - even a miss
        # or an overshoot, which move nobody but still deserve feedback.
        self._seq = 0
        # The last resolved dart, everything the board needs to animate one move:
        # where the token started, where the dart put it, and where any ladder or
        # snake then carried it. Rebuilt deterministically on replay.
        self.last_move: dict | None = None
        for player in self.players:
            player.score = 0     # square 0 is the start line, before square 1
            player.stats = {"square": 0, "climbs": 0, "slides": 0}

    # ------------------------------------------------------------ one dart

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        self._seq += 1
        spaces = movement_for(max(0, dart.score), self.divisor)
        start = player.score
        base = {
            "seq": self._seq,
            "player_id": player.player_id,
            "dart_label": dart.label,
            "dart_score": dart.score,
            "spaces": spaces,
            "from": start,
        }

        # A miss (or a genuine zero) still consumes the dart - it just moves nobody.
        if spaces == 0:
            self.last_move = {**base, "type": "miss", "landed": start, "to": start}
            return TurnResult(message=f"{player.name}: {dart.label} — no move.")

        landing = start + spaces
        # Exact finish: a dart that would sail past 100 doesn't move the token at
        # all, but it is still used up. This is what makes the run-in tactical.
        if landing > FINISH:
            need = FINISH - start
            self.last_move = {**base, "type": "overshoot", "landed": start, "to": start, "need": need}
            return TurnResult(
                message=f"TOO HIGH! {player.name} needs exactly {need} — {dart.label} "
                        f"would move {spaces}, so no move.",
                highlight="bad",
            )

        # Land on the square, then take any ladder or snake leading off it.
        square = landing
        kind = "move"
        if square in LADDERS:
            square = LADDERS[square]
            kind = "ladder"
            player.stats["climbs"] += 1
        elif square in SNAKES:
            square = SNAKES[square]
            kind = "snake"
            player.stats["slides"] += 1

        player.score = square
        player.stats["square"] = square

        if square >= FINISH:
            self.last_move = {**base, "type": "win", "landed": landing, "to": square}
            return self._win(player, dart)

        self.last_move = {**base, "type": kind, "landed": landing, "to": square}
        if kind == "ladder":
            return TurnResult(message=f"LADDER! {player.name} climbs {landing} → {square}.",
                              cue="score.180", highlight="big")
        if kind == "snake":
            return TurnResult(message=f"SNAKE! {player.name} slides {landing} → {square}.",
                              cue="bust", highlight="bad")
        return TurnResult(message=f"{player.name}: {dart.label} moves {spaces} to square {square}.",
                          highlight="good")

    def _win(self, player: PlayerState, dart: Dart) -> TurnResult:
        # A race: the first token home wins outright the moment it lands, even on
        # the first dart of a turn; the rest place by how far along they are.
        # Mirrors DonkeyDerby's finish so replay stays exact.
        self.finished = True
        self.winner_id = player.player_id
        player.finished = True
        player.place = 1
        trailing = sorted(
            (p for p in self.players if p is not player),
            key=lambda p: p.score, reverse=True,
        )
        for place, other in enumerate(trailing, 2):
            other.finished = True
            other.place = place
        return TurnResult(finished=True, cue="game.win", highlight="big",
                          message=f"{player.name} lands on {FINISH} and wins the race!")

    # ------------------------------------------------------------ presentation

    def target_hint(self, player: PlayerState) -> str | None:
        remaining = FINISH - player.score
        return (f"Square {player.score} — each dart moves you ⌈score ÷ {self.divisor}⌉ squares. "
                f"Land exactly on {FINISH}: {remaining} to go.")

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        # Every bed counts toward the move, so there is nothing to single out.
        return []

    def theme(self) -> str:
        return "classic"

    def view(self) -> dict:
        return {
            "kind": "snakes",
            "finish": FINISH,
            "columns": COLUMNS,
            "divisor": self.divisor,
            "ladders": LADDERS,
            "snakes": SNAKES,
            "positions": {p.player_id: p.score for p in self.players},
            "last_move": self.last_move,
        }
