"""Killer, Donkey Derby and Space Invaders - the games built for a room
full of people rather than a practice session.

Killer and Space Invaders follow the arcade presentation ported from
alternative-project: Killer assigns each player a *group* of physically
adjacent slices (1/2/3 depending on difficulty) and uses marks-then-hunt
rules; Space Invaders is a lane shooter - a fleet of aliens orbits the
board in three rows and darts fire shots down the numbered lanes.

Both games need randomness (target draws, fleet advances). The engine's
undo rebuilds a match by replaying the action log through a fresh game
instance constructed with the *same options dict*, so every random draw is
seeded from an options["seed"] that the first construction writes into the
dict - a replayed game deals the identical targets and advances.
"""
from __future__ import annotations

import random

from .base import Dart, Game, PlayerState, TurnResult
from .registry import register

# The physical board order, clockwise from the top.
SEGMENTS_CLOCKWISE = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]

# Numbers handed out to players, ordered so neighbours aren't adjacent on
# the board - used by Donkey Derby.
ASSIGNABLE = [20, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5, 1, 18, 4, 13, 6, 10, 15, 2, 17]


def _assign_numbers(players: list[PlayerState], seed: int = 0) -> dict[str, int]:
    pool = ASSIGNABLE[:]
    if seed:
        random.Random(seed).shuffle(pool)
    return {p.player_id: pool[i % len(pool)] for i, p in enumerate(players)}


# ---------------------------------------------------------------- killer

# How many adjacent slices make up one player's target group, and how many
# players fit before groups would overlap on the 20-slice board.
KILLER_SLICES = {"easy": 3, "normal": 2, "hard": 1}
KILLER_PLAYER_LIMITS = {"easy": 6, "normal": 10, "hard": 12}
KILLER_MARKS_TO_KILL = 3
KILLER_LIVES = 3


def killer_targets(number: int, slices: int) -> list[int]:
    """The centre number plus its physical neighbours, per difficulty."""
    index = SEGMENTS_CLOCKWISE.index(int(number))
    previous = SEGMENTS_CLOCKWISE[(index - 1) % 20]
    following = SEGMENTS_CLOCKWISE[(index + 1) % 20]
    if slices >= 3:
        return [previous, int(number), following]
    if slices == 2:
        return [previous, int(number)]
    return [int(number)]


def killer_assignments(count: int, difficulty: str, rng: random.Random) -> list[list[int]]:
    """Physically adjacent, mutually disjoint target groups for each player."""
    order = SEGMENTS_CLOCKWISE
    slices = KILLER_SLICES.get(difficulty, 1)
    if slices == 1:
        centres = rng.sample(order, count)
    elif slices == 2:
        parity = rng.randrange(2)
        centres = [order[(parity + step * 2) % 20] for step in range(10)]
        rng.shuffle(centres)
        centres = centres[:count]
    else:
        start = rng.randrange(20)
        centres = [order[(start + 1 + step * 3) % 20] for step in range(6)]
        rng.shuffle(centres)
        centres = centres[:count]
    return [killer_targets(number, slices) for number in centres]


def _targets_label(targets: list[int]) -> str:
    return " & ".join(str(n) for n in targets)


@register("killer")
class Killer(Game):
    slug = "killer"
    name = "Killer"

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        limit = KILLER_PLAYER_LIMITS.get(difficulty, 12)
        if len(players) > limit:
            slices = KILLER_SLICES.get(difficulty, 1)
            raise ValueError(
                f"Killer with {slices}-slice targets supports up to {limit} players "
                f"without overlapping targets"
            )
        self.lives = int(self.options.get("lives", KILLER_LIVES))
        seed = self.options.setdefault("seed", random.randrange(1 << 30))
        rng = random.Random(seed)
        assignments = killer_assignments(len(players), difficulty, rng)
        self.targets: dict[str, list[int]] = {}
        for player, targets in zip(players, assignments):
            self.targets[player.player_id] = targets
            player.score = self.lives
            player.stats = {"targets": targets, "marks": 0, "killer": False, "kills": 0}

    def _victim(self, segment: int, attacker: PlayerState) -> PlayerState | None:
        for player in self.active_players():
            if player is attacker:
                continue
            if segment in self.targets[player.player_id]:
                return player
        return None

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        if dart.segment is None or dart.multiplier <= 0 or dart.segment == 25:
            return TurnResult()
        segment = dart.segment
        own = self.targets[player.player_id]

        # Earning marks: any ring counts, doubles/trebles are worth more.
        if segment in own and not player.stats["killer"]:
            gained = min(dart.multiplier, KILLER_MARKS_TO_KILL - player.stats["marks"])
            player.stats["marks"] = min(KILLER_MARKS_TO_KILL, player.stats["marks"] + dart.multiplier)
            if player.stats["marks"] >= KILLER_MARKS_TO_KILL:
                player.stats["killer"] = True
                return TurnResult(message=f"{player.name} is a KILLER!", cue="bullseye", highlight="big")
            left = KILLER_MARKS_TO_KILL - player.stats["marks"]
            return TurnResult(
                message=f"+{gained} mark{'s' if gained != 1 else ''} — {left} more to become a Killer",
                highlight="good",
            )

        if not player.stats["killer"]:
            if self._victim(segment, player) is not None:
                return TurnResult(message="Become a Killer first!")
            return TurnResult()

        victim = self._victim(segment, player)
        if victim is None:
            return TurnResult()
        removed = min(dart.multiplier, victim.score)
        victim.score = max(0, victim.score - dart.multiplier)
        player.stats["kills"] += removed
        if victim.score <= 0:
            self.eliminate(victim)
            if self.finished:
                survivors = [p for p in self.players if p.place == 1]
                winner = survivors[0].name if survivors else player.name
                return TurnResult(finished=True, message=f"{winner} wins the Killer game!",
                                  cue="game.win", highlight="big")
            return TurnResult(message=f"{victim.name} is ELIMINATED!", cue="bust", highlight="big")
        return TurnResult(
            message=f"{victim.name} loses {removed} {'life' if removed == 1 else 'lives'}!",
            cue="bust", highlight="good",
        )

    def target_hint(self, player: PlayerState) -> str | None:
        own = _targets_label(self.targets[player.player_id])
        if not player.stats["killer"]:
            left = KILLER_MARKS_TO_KILL - player.stats["marks"]
            return f"Hit {own} — {left} more mark{'s' if left != 1 else ''} to become a Killer"
        rivals = [p for p in self.active_players() if p is not player]
        if not rivals:
            return None
        return "Hunt: " + ", ".join(_targets_label(self.targets[p.player_id]) for p in rivals)

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        # Before you're a killer only your own slices matter; after, every
        # living rival's slices are targets.
        if not player.stats.get("killer"):
            return list(self.targets[player.player_id])
        out: list[int] = []
        for p in self.active_players():
            if p is not player:
                out.extend(self.targets[p.player_id])
        return out

    def theme(self) -> str:
        return "killer"

    def view(self) -> dict:
        return {
            "kind": "killer",
            "difficulty": self.difficulty,
            "slices": KILLER_SLICES.get(self.difficulty, 1),
            "max_lives": self.lives,
            "marks_to_kill": KILLER_MARKS_TO_KILL,
            "targets": self.targets,
            "marks": {p.player_id: p.stats["marks"] for p in self.players},
            "killers": [p.player_id for p in self.players if p.stats.get("killer")],
        }


# ---------------------------------------------------------------- derby

@register("donkey-derby")
class DonkeyDerby(Game):
    slug = "donkey-derby"
    name = "Donkey Derby"

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        self.track = int(self.options.get("track", 12))
        self.numbers = _assign_numbers(self.players)
        for player in self.players:
            player.score = 0   # steps travelled
            player.stats = {"number": self.numbers[player.player_id]}

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        own = self.numbers[player.player_id]
        steps = 0
        if dart.hits(own):
            steps = {1: 1, 2: 2, 3: 3}.get(dart.multiplier, 0)
        elif dart.is_bull or dart.is_outer_bull:
            steps = 1   # wildcard

        if steps == 0:
            return TurnResult()

        player.score = min(player.score + steps, self.track)
        if player.score >= self.track:
            self.finish_player(player)
            return TurnResult(finished=True, message=f"{player.name}'s donkey wins the race!",
                              cue="game.win", highlight="big")
        gallop = {1: "Trot", 2: "Canter", 3: "GALLOP"}[steps]
        return TurnResult(message=f"{gallop}! {player.score}/{self.track}", highlight="good")

    def target_hint(self, player: PlayerState) -> str | None:
        return f"Hit {self.numbers[player.player_id]} to move (bull = wildcard)"

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        return [self.numbers[player.player_id], 25]

    def theme(self) -> str:
        return "derby"

    def view(self) -> dict:
        return {
            "kind": "derby",
            "track": self.track,
            "numbers": self.numbers,
            "positions": {p.player_id: p.score for p in self.players},
        }


# ---------------------------------------------------------------- invaders

# Aliens per orbital row (row 1 = closest to the board), per difficulty.
INVADER_FORMATIONS = {
    "easy": (10, 6, 4),      # 20 aliens
    "normal": (15, 10, 6),   # 31 aliens
    "hard": (20, 15, 10),    # 45 aliens
}
INVADER_LIVES = 3            # shared defence-grid lives
ADVANCES_PER_ROUND = 2       # aliens that creep closer after each full round
ADVANCES_TO_BREACH = 3       # advances before an alien breaches the grid


def _spread_lanes(count: int, offset: int) -> list[int]:
    """Distribute a partial row evenly around the physical dartboard."""
    order = SEGMENTS_CLOCKWISE
    return [order[(offset + (index * len(order)) // count) % len(order)] for index in range(count)]


def build_formation(difficulty: str) -> list[dict]:
    """Three readable orbital rows with exactly two armoured back-row tanks."""
    aliens: list[dict] = []
    counts = INVADER_FORMATIONS.get(difficulty, INVADER_FORMATIONS["normal"])
    for row, count in enumerate(counts, start=1):
        lanes = _spread_lanes(count, row - 1)
        tank_positions = {0, len(lanes) // 2} if row == 3 else set()
        for position, lane in enumerate(lanes):
            tank = row == 3 and position in tank_positions
            max_hp = 2 if tank else 1
            alien_type = "tank" if tank else ("heavy" if row == 3 else "fighter" if row == 2 else "scout")
            aliens.append({
                "id": f"row-{row}-lane-{lane}",
                "lane": lane,
                "row": row,
                "type": alien_type,
                "hp": max_hp,
                "max_hp": max_hp,
                "points": 6 if tank else row,
                "advance": 0,
                "alive": True,
                "destroyed": False,
                "breached": False,
            })
    return aliens


@register("space-invaders")
class SpaceInvaders(Game):
    slug = "space-invaders"
    name = "Space Invaders"

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        if len(players) > 6:
            raise ValueError("Space Invaders supports up to six players")
        raw_limit = self.options.get("round_limit")
        self.round_limit = int(raw_limit) if raw_limit else None
        seed = self.options.setdefault("seed", random.randrange(1 << 30))
        self._rng = random.Random(seed)
        self.aliens = build_formation(difficulty)
        self.invasion_lives = INVADER_LIVES
        self.completed_rounds = 0
        self.result: str | None = None       # victory | defeat | round_limit
        self.last_attack: dict | None = None
        self.last_advance: list[dict] = []
        self._attack_counter = 0
        for player in self.players:
            player.score = 0
            player.stats = {"kills": 0, "cannon": False}

    # ------------------------------------------------ combat

    def _alive(self) -> list[dict]:
        return [a for a in self.aliens if a["alive"]]

    def _damage_lane(self, lane: int, shots: int, player: PlayerState) -> tuple[int, list[str], list[str]]:
        """Apply shots front-to-back; surplus shots continue into the next alien."""
        landed = 0
        damaged: list[str] = []
        destroyed: list[str] = []
        for _ in range(max(0, shots)):
            targets = sorted(
                (a for a in self.aliens if a["alive"] and a["lane"] == lane),
                key=lambda a: a["row"],
            )
            if not targets:
                break
            target = targets[0]
            target["hp"] -= 1
            landed += 1
            damaged.append(target["id"])
            if target["hp"] <= 0:
                target["alive"] = False
                target["destroyed"] = True
                player.score += target["points"]
                player.stats["kills"] += 1
                destroyed.append(target["id"])
        return landed, damaged, destroyed

    def _finish_game(self, result: str) -> None:
        self.result = result
        self.finished = True
        # Places by score (kills as tiebreak) - defeat still ranks players,
        # but the "winner" of a lost defence is nobody.
        ranked = sorted(self.players, key=lambda p: (-p.score, -p.stats["kills"]))
        for player in ranked:
            self.finish_player(player)
        if result == "defeat":
            self.winner_id = None
        else:
            self.winner_id = ranked[0].player_id if ranked else None

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        if self.finished:
            return TurnResult()
        self._attack_counter += 1
        lanes: list[int] = []
        landed = 0
        damaged: list[str] = []
        destroyed: list[str] = []
        attack_type = "miss"
        cannon_fired = False

        if dart.is_bull:
            # Inner bull: one pulse down every numbered lane.
            attack_type = "bull_barrage"
            lanes = list(SEGMENTS_CLOCKWISE)
            for lane in lanes:
                hits, dmg, kills = self._damage_lane(lane, 1, player)
                landed += hits
                damaged.extend(dmg)
                destroyed.extend(kills)
        elif dart.is_outer_bull:
            attack_type = "charge"
            player.stats["cannon"] = True
        elif dart.segment in SEGMENTS_CLOCKWISE and dart.multiplier > 0:
            attack_type = "normal"
            lanes = [dart.segment]
            if player.stats.get("cannon"):
                position = SEGMENTS_CLOCKWISE.index(dart.segment)
                lanes = [
                    SEGMENTS_CLOCKWISE[(position - 1) % 20],
                    dart.segment,
                    SEGMENTS_CLOCKWISE[(position + 1) % 20],
                ]
                player.stats["cannon"] = False
                cannon_fired = True
                attack_type = "multi_cannon"
            for lane in lanes:
                hits, dmg, kills = self._damage_lane(lane, dart.multiplier, player)
                landed += hits
                damaged.extend(dmg)
                destroyed.extend(kills)

        points = sum(a["points"] for a in self.aliens if a["id"] in set(destroyed))
        self.last_attack = {
            "id": f"attack-{self._attack_counter}",
            "type": attack_type,
            "player_id": player.player_id,
            "label": dart.label,
            "origin_lane": dart.segment if dart.segment in SEGMENTS_CLOCKWISE else None,
            "lanes": lanes,
            "damaged_ids": list(dict.fromkeys(damaged)),
            "destroyed_ids": destroyed,
            "points": points,
            "kills": len(destroyed),
        }

        if not self._alive():
            self._finish_game("victory")
            best = max(self.players, key=lambda p: (p.score, p.stats["kills"]))
            return TurnResult(finished=True, cue="game.win", highlight="big",
                              message=f"FLEET CLEARED! {best.name} leads the defence with {best.score} points")

        if attack_type == "bull_barrage":
            return TurnResult(message=f"BULL BARRAGE! {len(destroyed)} aliens destroyed, +{points} points",
                              cue="score.180", highlight="big")
        if attack_type == "charge":
            return TurnResult(message="MULTI-CANNON ARMED — your next numbered hit fires three lanes",
                              highlight="good")
        if cannon_fired:
            lane_label = " / ".join(str(l) for l in lanes)
            return TurnResult(message=f"MULTI-CANNON across {lane_label}: {len(destroyed)} destroyed, +{points} points",
                              highlight="big")
        if landed:
            word = "DIRECT HIT" if len(destroyed) == 1 else "HIT"
            return TurnResult(message=f"{word}! Lane {dart.segment}: {len(destroyed)} destroyed, +{points} points",
                              highlight="good")
        if attack_type == "normal":
            return TurnResult(message=f"Lane {dart.segment} is clear")
        return TurnResult()

    # ------------------------------------------------ fleet advance

    def _advance_fleet(self) -> None:
        living = self._alive()
        count = min(ADVANCES_PER_ROUND, len(living))
        selected = self._rng.sample(living, count) if count else []
        advances: list[dict] = []
        breaches = 0
        for alien in selected:
            previous = alien["advance"]
            alien["advance"] = previous + 1
            breached = alien["advance"] >= ADVANCES_TO_BREACH
            if breached:
                alien["alive"] = False
                alien["breached"] = True
                breaches += 1
            advances.append({"id": alien["id"], "lane": alien["lane"], "row": alien["row"],
                             "from": previous, "to": alien["advance"], "breached": breached})
        self.last_advance = advances
        if breaches:
            self.invasion_lives = max(0, self.invasion_lives - breaches)

    def on_turn_end(self, player: PlayerState, darts: list[Dart]) -> TurnResult | None:
        if self.finished:
            return None
        active = self.active_players()
        if not active or player is not active[-1]:
            return None
        # Full round complete: the fleet moves.
        self.completed_rounds += 1
        self._advance_fleet()
        if self.invasion_lives <= 0:
            self._finish_game("defeat")
            return TurnResult(finished=True, cue="bust", highlight="bad",
                              message="THE INVASION BROKE THROUGH — defence grid lost")
        if not self._alive():
            self._finish_game("victory")
            best = max(self.players, key=lambda p: (p.score, p.stats["kills"]))
            return TurnResult(finished=True, cue="game.win", highlight="big",
                              message=f"FLEET CLEARED! {best.name} wins with {best.score} points")
        if self.round_limit is not None and self.completed_rounds >= self.round_limit:
            self._finish_game("round_limit")
            best = max(self.players, key=lambda p: (p.score, p.stats["kills"]))
            return TurnResult(finished=True, cue="game.win", highlight="big",
                              message=f"Round limit reached — {best.name} wins on points")
        advanced = len(self.last_advance)
        if advanced:
            return TurnResult(message=f"{advanced} alien{'s' if advanced != 1 else ''} advanced!",
                              highlight="bad")
        return None

    def target_hint(self, player: PlayerState) -> str | None:
        if player.stats.get("cannon"):
            return "MULTI-CANNON READY — hit a numbered lane to fire three lanes"
        alive = self._alive()
        if not alive:
            return None
        lanes = sorted({a["lane"] for a in alive}, key=SEGMENTS_CLOCKWISE.index)
        return "Fire into lanes: " + ", ".join(str(l) for l in lanes[:8])

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        return sorted({a["lane"] for a in self._alive()}) + [25]

    def theme(self) -> str:
        return "space"

    def view(self) -> dict:
        return {
            "kind": "invaders",
            "difficulty": self.difficulty,
            "round_limit": self.round_limit,
            "completed_rounds": self.completed_rounds,
            "invasion_lives": self.invasion_lives,
            "aliens": self.aliens,
            "alien_total": len(self.aliens),
            "aliens_remaining": len(self._alive()),
            "aliens_destroyed": sum(1 for a in self.aliens if a["destroyed"]),
            "last_attack": self.last_attack,
            "last_advance": self.last_advance,
            "result": self.result,
            "cannons": {p.player_id: bool(p.stats.get("cannon")) for p in self.players},
            "kills": {p.player_id: p.stats["kills"] for p in self.players},
        }
