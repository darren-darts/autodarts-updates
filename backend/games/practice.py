"""Round the Clock and Shanghai - the two 'work through the numbers' games."""
from __future__ import annotations

from .base import Dart, Game, PlayerState, TurnResult
from .registry import register

BULL_TARGET = 21  # one past 20: the final target in Round the Clock


@register("round-the-clock")
class RoundTheClock(Game):
    slug = "round-the-clock"
    name = "Round the Clock"

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        self.require = self.options.get("require", "any")   # "any" | "double"
        self.bonus = bool(self.options.get("bonus", False))  # doubles/trebles skip ahead
        for player in self.players:
            player.score = 1          # score doubles as "current target"
            player.stats = {"darts": 0, "hits": 0}

    def _matches(self, dart: Dart, target: int) -> bool:
        if target == BULL_TARGET:
            return dart.segment == 25 and (dart.multiplier == 2 or self.require != "double")
        if not dart.hits(target):
            return False
        return dart.multiplier == 2 if self.require == "double" else True

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        player.stats["darts"] += 1
        target = player.score
        if not self._matches(dart, target):
            return TurnResult()

        player.stats["hits"] += 1
        step = 1
        if self.bonus and target != BULL_TARGET:
            step = {2: 2, 3: 3}.get(dart.multiplier, 1)
        player.score = min(player.score + step, BULL_TARGET + 1)

        if player.score > BULL_TARGET:
            self.finish_player(player)
            return TurnResult(finished=True, message=f"{player.name} wins!", cue="game.win", highlight="big")
        nxt = "BULL" if player.score == BULL_TARGET else str(player.score)
        extra = f" (+{step})" if step > 1 else ""
        return TurnResult(message=f"Hit!{extra} Now on {nxt}", highlight="good")

    def target_hint(self, player: PlayerState) -> str | None:
        if player.score == BULL_TARGET:
            return "BULLSEYE to finish"
        prefix = "Double " if self.require == "double" else ""
        return f"{prefix}{player.score}"

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        return [25] if player.score == BULL_TARGET else [player.score]

    def view(self) -> dict:
        return {
            "kind": "clock",
            "final": BULL_TARGET,
            "require": self.require,
            "targets": {p.player_id: p.score for p in self.players},
        }


@register("shanghai")
class Shanghai(Game):
    slug = "shanghai"
    name = "Shanghai"

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        self.first_round = int(self.options.get("start_round", 1))
        self.last_round = int(self.options.get("rounds", 20))
        # Deliberately NOT `self.round`: the engine owns that attribute and
        # increments it every time the turn order wraps back to the first
        # player. Shanghai also advances at the end of a full cycle, so
        # sharing the name made the target jump two numbers per round -
        # round 1 went straight to 3, and half the board was never thrown at.
        self.target_round = self.first_round
        for player in self.players:
            player.score = 0
            player.stats = {"darts": 0, "shanghai": False}
        self._turn_rings: set[int] = set()

    @property
    def target(self) -> int:
        return self.target_round

    def on_turn_start(self, player: PlayerState) -> TurnResult | None:
        self._turn_rings = set()
        return None

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        if dart_index == 0:
            self._turn_rings = set()
        player.stats["darts"] += 1
        if not dart.hits(self.target):
            return TurnResult()

        player.score += dart.score
        self._turn_rings.add(dart.multiplier)
        # single + double + treble of the target in one turn is an instant win
        if {1, 2, 3}.issubset(self._turn_rings):
            player.stats["shanghai"] = True
            self.finish_player(player)
            return TurnResult(finished=True, message=f"SHANGHAI! {player.name} wins!",
                              cue="game.win", highlight="big")
        return TurnResult(message=f"+{dart.score}", highlight="good")

    def on_turn_end(self, player: PlayerState, darts: list[Dart]) -> TurnResult | None:
        # The round advances only after the last player of the round throws.
        active = self.active_players()
        if active and player is active[-1]:
            if self.target_round >= self.last_round:
                best = max(self.players, key=lambda p: p.score)
                for p in sorted(self.players, key=lambda p: -p.score):
                    self.finish_player(p)
                self.finished = True
                self.winner_id = best.player_id
                return TurnResult(finished=True, message=f"{best.name} wins with {best.score}!",
                                  cue="game.win", highlight="big")
            self.target_round += 1
        return None

    def target_hint(self, player: PlayerState) -> str | None:
        return f"Round {self.target_round} - throw at {self.target}"

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        return [self.target]

    def view(self) -> dict:
        return {
            "kind": "shanghai",
            "target": self.target,
            "target_round": self.target_round,
            "first_round": self.first_round,
            "last_round": self.last_round,
            "rings_this_turn": sorted(self._turn_rings),
        }
