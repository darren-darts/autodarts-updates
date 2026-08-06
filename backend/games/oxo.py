"""Noughts & Crosses - claim squares on a 3x3 grid of board targets.

Head-to-head only: player 1 is X, player 2 is O, and the first to own three
squares in a line wins. The grid is the classic fixed layout (bull in the
centre, a spread of numbers around it) so the game is identical every time -
deliberately, since replay/undo rebuilds the game from scratch and a random
layout would need the seed dance in party.py for no real gain.

Where the difficulty knobs live:
    claim              "any" | "double_or_treble"  - which rings claim a square
    end_turn_after_claim  bool  - Standard mode: a claim ends your turn
    inner_bull_only    bool  - the centre needs the 50, not just the 25
"""
from __future__ import annotations

from .base import Dart, Game, PlayerState, TurnResult
from .registry import register

# Fixed grid, row-major: index 0 is top-left, 8 is bottom-right. 25 = bull.
GRID_TARGETS = [12, 18, 4, 9, 25, 14, 16, 7, 20]
CENTRE = 4

LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),   # rows
    (0, 3, 6), (1, 4, 7), (2, 5, 8),   # columns
    (0, 4, 8), (2, 4, 6),              # diagonals
]

SYMBOLS = ("X", "O")

# Tactical suggestion order once win/block/centre are settled: corners first,
# then edges. Fixed order rather than random - see the determinism rule in
# ADDING_A_GAME.md; a coin-flip here would make undo rebuild a different hint.
CORNERS = (0, 2, 6, 8)
EDGES = (1, 3, 5, 7)


def _label(target: int) -> str:
    return "BULL" if target == 25 else str(target)


@register("noughts-and-crosses")
class NoughtsAndCrosses(Game):
    slug = "noughts-and-crosses"
    name = "Noughts & Crosses"

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        if len(players) != 2:
            raise ValueError("Noughts & Crosses is a two-player game")
        self.claim = self.options.get("claim", "any")           # "any" | "double_or_treble"
        self.end_turn_after_claim = bool(self.options.get("end_turn_after_claim", False))
        self.inner_bull_only = bool(self.options.get("inner_bull_only", False))
        self.owners: list[str | None] = [None] * 9
        self.winning_line: tuple[int, int, int] | None = None
        self.symbols: dict[str, str] = {}
        for player, symbol in zip(self.players, SYMBOLS):
            self.symbols[player.player_id] = symbol
            player.score = 0                       # squares owned
            player.stats = {"symbol": symbol, "claims": [], "darts": 0}

    # ------------------------------------------------------------ helpers

    def _square_for(self, dart: Dart) -> int | None:
        """Grid index this dart's segment belongs to, claimed or not."""
        if dart.segment is None or dart.multiplier <= 0:
            return None
        try:
            return GRID_TARGETS.index(dart.segment)
        except ValueError:
            return None

    def _owner_name(self, index: int) -> str:
        owner_id = self.owners[index]
        for player in self.players:
            if player.player_id == owner_id:
                return player.name
        return "someone"

    def _wins(self, player_id: str, owners: list[str | None]) -> tuple[int, int, int] | None:
        for line in LINES:
            if all(owners[i] == player_id for i in line):
                return line
        return None

    def _multiplier_ok(self, dart: Dart, index: int) -> bool:
        if index == CENTRE:
            return dart.multiplier == 2 if self.inner_bull_only else dart.multiplier in (1, 2)
        if self.claim == "double_or_treble":
            return dart.multiplier in (2, 3)
        return dart.multiplier >= 1

    # ------------------------------------------------------------ scoring

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        player.stats["darts"] += 1
        index = self._square_for(dart)

        if dart.is_miss:
            return TurnResult(message="Miss - no square claimed")
        if index is None:
            return TurnResult(message=f"Hit {dart.segment} - that number is not in play")
        target = _label(GRID_TARGETS[index])
        if self.owners[index] is not None:
            who = "you" if self.owners[index] == player.player_id else self._owner_name(index)
            return TurnResult(message=f"Hit {target} - that square is already owned by {who}")
        if not self._multiplier_ok(dart, index):
            need = "the INNER bull" if index == CENTRE else "a double or treble"
            return TurnResult(message=f"Hit {target}, but {need} is required", highlight="bad")

        # Claim it.
        self.owners[index] = player.player_id
        player.score += 1
        player.stats["claims"].append(index)
        symbol = self.symbols[player.player_id]

        line = self._wins(player.player_id, self.owners)
        if line:
            self.winning_line = line
            self.finish_player(player)     # winner takes place 1, loser auto-finishes
            return TurnResult(
                finished=True, end_turn=True, cue="game.win", highlight="big",
                message=f"{player.name} wins with three {symbol}s in a row!",
            )

        if all(owner is not None for owner in self.owners):
            self.finished = True
            return TurnResult(
                finished=True, end_turn=True, highlight="big",
                message="The grid is full - it's a draw!",
            )

        if self.end_turn_after_claim:
            return TurnResult(
                end_turn=True, highlight="good",
                message=f"{target} claimed - a claim ends your turn",
            )
        return TurnResult(message=f"Hit {target} - {player.name} claims the square", highlight="good")

    # ------------------------------------------------------------ tactics

    def _recommended(self, player_id: str) -> int | None:
        """Win now, else block, else centre, else corner, else edge."""
        available = [i for i in range(9) if self.owners[i] is None]
        if not available:
            return None
        opponent_id = next(pid for pid in self.symbols if pid != player_id)
        for candidate_id in (player_id, opponent_id):
            for i in available:
                trial = list(self.owners)
                trial[i] = candidate_id
                if self._wins(candidate_id, trial):
                    return i
        if CENTRE in available:
            return CENTRE
        for i in CORNERS:
            if i in available:
                return i
        for i in EDGES:
            if i in available:
                return i
        return available[0]

    # ------------------------------------------------------------ presentation

    def target_hint(self, player: PlayerState) -> str | None:
        if self.finished:
            return None
        index = self._recommended(player.player_id)
        if index is None:
            return None
        target = _label(GRID_TARGETS[index])
        if index == CENTRE and self.inner_bull_only:
            return f"Aim for the INNER bull ({self.symbols[player.player_id]})"
        ring = " - double or treble only" if self.claim == "double_or_treble" and index != CENTRE else ""
        return f"Aim for {target}{ring}"

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        if self.finished:
            return [GRID_TARGETS[i] for i in (self.winning_line or [])]
        return [GRID_TARGETS[i] for i in range(9) if self.owners[i] is None]

    def view(self) -> dict:
        return {
            "kind": "oxo",
            "squares": [
                {
                    "target": GRID_TARGETS[i],
                    "label": _label(GRID_TARGETS[i]),
                    "owner": self.symbols.get(self.owners[i]) if self.owners[i] else None,
                }
                for i in range(9)
            ],
            "symbols": self.symbols,
            "winning_line": list(self.winning_line) if self.winning_line else [],
            # view() does not know whose turn it is (the engine owns that), so
            # the tactical suggestion is computed for both players and the UI
            # reads the current player's entry from state.current_player_id.
            "suggested": {} if self.finished else {
                pid: self._recommended(pid) for pid in self.symbols
            },
            "claim": self.claim,
            "inner_bull_only": self.inner_bull_only,
            "end_turn_after_claim": self.end_turn_after_claim,
        }
