"""X01 - 301/501/701, the standard game."""
from __future__ import annotations

from .base import Dart, Game, PlayerState, TurnResult
from .registry import register

# Checkouts worth showing when a player is on a finish. Kept short on
# purpose: the point is a nudge for a casual player, not a full 170-entry
# lookup table nobody reads mid-throw.
CHECKOUTS = {
    170: "T20 T20 BULL", 167: "T20 T19 BULL", 164: "T20 T18 BULL", 161: "T20 T17 BULL",
    160: "T20 T20 D20", 158: "T20 T20 D19", 157: "T20 T19 D20", 156: "T20 T20 D18",
    155: "T20 T19 D19", 154: "T20 T18 D20", 153: "T20 T19 D18", 152: "T20 T20 D16",
    151: "T20 T17 D20", 150: "T20 T18 D18", 141: "T20 T15 D18", 140: "T20 T20 D10",
    138: "T20 T18 D12", 137: "T20 T19 D10", 136: "T20 T20 D8", 132: "T20 T16 D12",
    130: "T20 T18 D8", 129: "T19 T16 D12", 128: "T18 T14 D16", 126: "T19 T19 D6",
    124: "T20 T16 D8", 121: "T20 T11 D14", 120: "T20 20 D20", 118: "T20 18 D20",
    117: "T20 17 D20", 116: "T20 16 D20", 115: "T20 15 D20", 114: "T20 14 D20",
    113: "T20 13 D20", 112: "T20 12 D20", 111: "T20 11 D20", 110: "T20 BULL",
    109: "T20 9 D20", 108: "T20 16 D16", 107: "T19 BULL", 106: "T20 10 D18",
    105: "T20 13 D16", 104: "T18 BULL", 103: "T19 10 D18", 102: "T20 10 D16",
    101: "T17 BULL", 100: "T20 D20", 98: "T20 D19", 97: "T19 D20", 96: "T20 D18",
    95: "T19 D19", 94: "T18 D20", 93: "T19 D18", 92: "T20 D16", 91: "T17 D20",
    90: "T20 D15", 89: "T19 D16", 88: "T20 D14", 87: "T17 D18", 86: "T18 D16",
    85: "T15 D20", 84: "T20 D12", 83: "T17 D16", 82: "T14 D20", 81: "T19 D12",
    80: "T20 D10", 79: "T19 D11", 78: "T18 D12", 77: "T19 D10", 76: "T20 D8",
    75: "T17 D12", 74: "T14 D16", 73: "T19 D8", 72: "T16 D12", 71: "T13 D16",
    70: "T18 D8", 69: "T19 D6", 68: "T20 D4", 67: "T17 D8", 66: "T10 D18",
    65: "T19 D4", 64: "T16 D8", 63: "T13 D12", 62: "T10 D16", 61: "T15 D8",
    60: "20 D20", 58: "18 D20", 57: "17 D20", 56: "16 D20", 55: "15 D20",
    54: "14 D20", 53: "13 D20", 52: "12 D20", 51: "11 D20", 50: "BULL",
    48: "16 D16", 46: "6 D20", 44: "4 D20", 42: "10 D16", 40: "D20", 38: "D19",
    36: "D18", 34: "D17", 32: "D16", 30: "D15", 28: "D14", 26: "D13", 24: "D12",
    22: "D11", 20: "D10", 18: "D9", 16: "D8", 14: "D7", 12: "D6", 10: "D5",
    8: "D4", 6: "D3", 4: "D2", 2: "D1",
}


@register("x01")
class X01(Game):
    slug = "x01"
    name = "X01"

    def __init__(self, players: list[PlayerState], difficulty: str, options: dict | None = None):
        super().__init__(players, difficulty, options)
        self.start = int(self.options.get("start", 501))
        self.double_out = bool(self.options.get("double_out", True))
        self.double_in = bool(self.options.get("double_in", False))
        for player in self.players:
            player.score = self.start
            player.stats = {"darts": 0, "scored": 0, "best_turn": 0, "opened": not self.double_in}
        # score at the start of the current turn, to restore on a bust
        self._turn_start_score: dict[str, int] = {p.player_id: self.start for p in self.players}
        self._turn_points = 0

    def on_turn_start(self, player: PlayerState) -> TurnResult | None:
        self._turn_start_score[player.player_id] = player.score
        self._turn_points = 0
        return None

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        if dart_index == 0:
            self._turn_start_score[player.player_id] = player.score
            self._turn_points = 0
        player.stats["darts"] += 1

        # Double-in: nothing counts until the first double lands.
        if not player.stats["opened"]:
            if dart.multiplier == 2:
                player.stats["opened"] = True
            else:
                return TurnResult(message="Need a double to start")

        remaining = player.score - dart.score
        if remaining < 0 or remaining == 1 or (remaining == 0 and self.double_out and dart.multiplier != 2):
            player.score = self._turn_start_score[player.player_id]
            return TurnResult(end_turn=True, message="BUST!", cue="bust", highlight="bad")

        player.score = remaining
        player.stats["scored"] += dart.score
        self._turn_points += dart.score

        if remaining == 0:
            self.finish_player(player)
            return TurnResult(finished=True, message=f"{player.name} wins!", cue="game.win", highlight="big")
        return TurnResult(highlight="good" if dart.score >= 40 else None)

    def on_turn_end(self, player: PlayerState, darts: list[Dart]) -> TurnResult | None:
        player.stats["best_turn"] = max(player.stats.get("best_turn", 0), self._turn_points)
        if self._turn_points == 180:
            return TurnResult(message="ONE HUNDRED AND EIGHTY!", cue="score.180", highlight="big")
        if self._turn_points >= 100:
            return TurnResult(message=f"{self._turn_points}!", highlight="good")
        return None

    def target_hint(self, player: PlayerState) -> str | None:
        if not player.stats.get("opened"):
            return "Hit any double to start"
        checkout = CHECKOUTS.get(player.score)
        if checkout:
            return f"Checkout: {checkout}"
        if player.score > 170:
            return None
        return "No checkout - leave yourself a double"

    def view(self) -> dict:
        return {
            "kind": "x01",
            "start": self.start,
            "double_out": self.double_out,
            "double_in": self.double_in,
            "averages": {
                p.player_id: round(p.stats["scored"] / p.stats["darts"] * 3, 1) if p.stats["darts"] else 0.0
                for p in self.players
            },
        }
