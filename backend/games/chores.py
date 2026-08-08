"""Mr vs Mrs: Chore Challenge - throw for the washing up.

A chore appears, both players throw one dart, and the lower score does it.
Everything else in here is decoration on that one idea: Double Trouble makes a
chore count twice, a Lucky Target hands out a bonus, a Steal Round lets the
winner move a chore around, and the player who lost the most rounds spins the
Wheel of Misfortune at the end.

Two structural decisions are worth reading before changing anything.

**A round is one engine turn of two darts, not two turns of one.** The engine
requires the darts to come out of the board between turns, and one dart each
per turn would mean walking to the board twice per round. So the turn is the
whole round: dart 0 belongs to the engine's current player, dart 1 to their
opponent, and both darts come out together. `_thrower_for` is the only place
that mapping lives. A pleasant side effect is that the engine's normal turn
rotation alternates who throws first each round, which is the fair way round.

**Everything random is decided once, in `__init__`, from a stored seed.** Undo
and previous-player rebuild the match by replaying the dart log through a brand
new instance (see ADDING_A_GAME.md section 2, rule 2), so a game that rolled a
Double Trouble round mid-play would quietly become a different game after an
undo. The whole schedule - chore order, which rounds are doubled, stolen or
lucky, and the wheel result - is drawn up front in `_build_script` and simply
read from thereafter.
"""
from __future__ import annotations

import random

from .base import Dart, Game, PlayerState, TurnResult
from .registry import register

# The 12 starter chores. Order matters twice over: it is the order of the
# supplied artwork sheet (4 across, 3 down - see tools/slice_chore_images.py),
# and `id` is what the frontend looks for at /chores/<id>.png.
CHORES: list[dict[str, str]] = [
    {"id": "laundry", "label": "Laundry", "emoji": "\U0001f9fa"},
    {"id": "washing_up", "label": "Washing Up", "emoji": "\U0001f37d️"},
    {"id": "vacuuming", "label": "Vacuuming", "emoji": "\U0001f9f9"},
    {"id": "food_shopping", "label": "Food Shopping", "emoji": "\U0001f6d2"},
    {"id": "bins", "label": "Taking the Bins Out", "emoji": "\U0001f5d1️"},
    {"id": "bathroom", "label": "Clean the Bathroom", "emoji": "\U0001f6bd"},
    {"id": "cooking", "label": "Cooking Dinner", "emoji": "\U0001f373"},
    {"id": "dog_walk", "label": "Walking the Dog", "emoji": "\U0001f436"},
    {"id": "bedding", "label": "Change the Bedding", "emoji": "\U0001f6cf️"},
    {"id": "car_wash", "label": "Wash the Car", "emoji": "\U0001f697"},
    {"id": "gardening", "label": "Gardening", "emoji": "\U0001f331"},
    {"id": "kitchen", "label": "Clean the Kitchen", "emoji": "\U0001f9fd"},
]
CHORE_BY_ID = {chore["id"]: chore for chore in CHORES}

# Lucky Target bonuses. These are *held*, not applied: "skip one assigned
# chore" and "your partner picks" are decisions for the two people at the
# kitchen table, and the app has no way to ask which chore they mean. They are
# awarded, shown on the result screen, and settled between the players - which
# is how the printed rules describe them too.
BONUSES: list[dict[str, str]] = [
    {"key": "pass_token", "label": "Pass Token", "emoji": "⭐",
     "detail": "Skip one assigned chore."},
    {"key": "swap", "label": "Swap", "emoji": "\U0001f501",
     "detail": "Exchange one chore with your partner."},
    {"key": "partners_choice", "label": "Partner's Choice", "emoji": "❤️",
     "detail": "Your partner picks which remaining chore you do."},
    {"key": "mystery", "label": "Mystery Reward", "emoji": "\U0001f381",
     "detail": "A random advantage or penalty - settle it between you."},
]
BONUS_BY_KEY = {bonus["key"]: bonus for bonus in BONUSES}

# Steal Round powers. Unlike the bonuses above these ARE applied, because each
# one has an unambiguous target: the chore just assigned, or the winner's most
# recent one. A power that cannot apply (nothing to swap or remove yet) falls
# back to doubling, which always can.
STEAL_POWERS = ["swap", "remove", "double"]

# The Wheel of Misfortune, in the order it is drawn on screen.
WHEEL: list[dict[str, str]] = [
    {"key": "clean_oven", "label": "Clean the oven", "kind": "chore"},
    {"key": "deep_clean_bathroom", "label": "Deep clean the bathroom", "kind": "chore"},
    {"key": "takeaway", "label": "Takeaway night (lucky!)", "kind": "reward"},
    {"key": "breakfast_in_bed", "label": "Breakfast in bed", "kind": "reward"},
    {"key": "movie", "label": "Choose next week's movie", "kind": "reward"},
    {"key": "no_chores", "label": "No chores (jackpot!)", "kind": "jackpot"},
    {"key": "car_wash", "label": "Car wash", "kind": "chore"},
    {"key": "cook_sunday", "label": "Cook Sunday dinner", "kind": "chore"},
]

MIN_ROUNDS, MAX_ROUNDS = 5, 15
SEGMENTS = list(range(1, 21))


@register("mr-vs-mrs")
class ChoreChallenge(Game):
    slug = "mr-vs-mrs"
    name = "Mr vs Mrs: Chore Challenge"
    darts_per_turn = 2          # one dart each - see the module docstring

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        # Both UIs cap the picker at two, but the API does not enforce
        # max_players - and a third player here would silently never throw,
        # because every round is one dart each between two people.
        if len(players) != 2:
            raise ValueError("Mr vs Mrs is a two-player game")
        self.rounds = max(MIN_ROUNDS, min(MAX_ROUNDS, int(self.options.get("rounds", 10))))

        # setdefault is the important part: the first build writes the seed
        # into options, every rebuild reads the same one back out.
        seed = self.options.setdefault("seed", random.randrange(1 << 30))
        self.script = self._build_script(random.Random(seed))

        # NOT self.round - the engine owns that attribute and increments it
        # whenever turn order wraps (ADDING_A_GAME.md section 2, rule 3).
        self.chore_round = 1
        self.results: list[dict] = []                                  # decided rounds, in order
        self.assignments = {p.player_id: [] for p in self.players}     # chores to do
        self.bonuses = {p.player_id: [] for p in self.players}         # lucky target wins
        self.rewards = {p.player_id: [] for p in self.players}         # wheel rewards
        self.wheel: dict | None = None                                 # set when the game ends
        self.draw = False

        for player in self.players:
            player.score = 0        # rounds won - the big number on every screen
            player.stats = {"rounds_won": 0, "chores": 0, "bonuses": []}

        # Per-round scratch, rebuilt from dart 0 so a replay cannot inherit it.
        self._throws: list[tuple[str, int]] = []
        self._leader_id: str | None = None

    # ------------------------------------------------------------ setup

    def _build_script(self, rng: random.Random) -> dict:
        """Draw the whole game up front so replays are identical.

        Nothing here may be re-rolled later. If you add a random element, add
        it to this method, not to apply_dart.
        """
        # Chores are drawn without repeats. There are only 12 of them and up to
        # 15 rounds, so a long game reshuffles and starts a second lap rather
        # than running out - the repeat is marked in the round history.
        order: list[str] = []
        while len(order) < self.rounds:
            lap = [chore["id"] for chore in CHORES]
            rng.shuffle(lap)
            order.extend(lap)
        order = order[:self.rounds]

        every_round = range(1, self.rounds + 1)
        doubles = sorted(rng.sample(list(every_round), k=max(1, self.rounds // 4)))
        steals = sorted(rng.sample(list(every_round), k=rng.choice([1, 2])))
        lucky_rounds = sorted(rng.sample(list(every_round), k=max(1, self.rounds // 3)))

        # Sorted before iterating, so the sequence of rng calls is fixed.
        lucky = {
            round_no: {"segment": rng.choice(SEGMENTS), "bonus": rng.choice(BONUSES)["key"]}
            for round_no in lucky_rounds
        }
        powers = {round_no: rng.choice(STEAL_POWERS) for round_no in steals}
        return {
            "order": order,
            "doubles": doubles,
            "steals": steals,
            "lucky": lucky,
            "powers": powers,
            "wheel_index": rng.randrange(len(WHEEL)),
        }

    # ------------------------------------------------------------ helpers

    def _chore_for(self, round_no: int) -> dict:
        return CHORE_BY_ID[self.script["order"][round_no - 1]]

    @property
    def chore(self) -> dict:
        """The chore being thrown for right now."""
        return self._chore_for(min(self.chore_round, self.rounds))

    def _opponent(self, player_id: str) -> PlayerState:
        return next(p for p in self.players if p.player_id != player_id)

    def _player(self, player_id: str) -> PlayerState:
        return next(p for p in self.players if p.player_id == player_id)

    def _thrower_for(self, dart_index: int, leader: PlayerState) -> PlayerState:
        """Whose dart this is. The engine hands every dart in a turn to the
        current player; here the second one belongs to their opponent."""
        if dart_index % 2 == 0:
            return leader
        return self._opponent(leader.player_id)

    def _lucky(self, round_no: int) -> dict | None:
        return self.script["lucky"].get(round_no)

    def _chore_count(self, player: PlayerState) -> int:
        return sum(entry["count"] for entry in self.assignments[player.player_id])

    def _sync_stats(self) -> None:
        for player in self.players:
            player.stats = {
                "rounds_won": player.score,
                "chores": self._chore_count(player),
                "bonuses": [BONUS_BY_KEY[key]["label"] for key in self.bonuses[player.player_id]],
            }

    # ------------------------------------------------------------ play

    def on_turn_start(self, player: PlayerState) -> TurnResult | None:
        self._throws = []
        self._leader_id = player.player_id
        return None

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        if dart_index == 0:
            # Replay-safe reset: on_turn_start is not called when a partial
            # turn is replayed, so the first dart re-establishes the round.
            self._throws = []
            self._leader_id = player.player_id

        thrower = self._thrower_for(dart_index, player)
        self._throws.append((thrower.player_id, dart.score))

        lucky = self._lucky(self.chore_round)
        if lucky and dart.hits(lucky["segment"]):
            bonus = BONUS_BY_KEY[lucky["bonus"]]
            self.bonuses[thrower.player_id].append(bonus["key"])
            self._sync_stats()
            return TurnResult(
                message=f"LUCKY TARGET! {thrower.name} hits {dart.label} and wins {bonus['label']} - {bonus['detail']}",
                cue="bullseye",
                highlight="big",
            )

        waiting = self._opponent(thrower.player_id)
        if dart_index == 0:
            return TurnResult(message=f"{thrower.name} throws {dart.label} for {dart.score}. {waiting.name} to throw.")
        return TurnResult(message=f"{thrower.name} throws {dart.label} for {dart.score}.")

    def on_turn_end(self, player: PlayerState, darts: list[Dart]) -> TurnResult | None:
        """Both darts are in, so the round can be settled."""
        if self.finished or len(self._throws) < 2:
            # A turn cut short by hand (Next player after one dart) leaves the
            # round undecided - the same chore simply comes round again.
            return None

        (first_id, first_score), (second_id, second_score) = self._throws[:2]
        if first_score == second_score:
            return TurnResult(
                message=f"TIE on {first_score}! Sudden death - both throw again for {self.chore['label']}.",
                highlight="bad",
            )

        winner = self._player(first_id if first_score > second_score else second_id)
        loser = self._opponent(winner.player_id)
        return self._settle(winner, loser, max(first_score, second_score), min(first_score, second_score))

    def _settle(self, winner: PlayerState, loser: PlayerState, high: int, low: int) -> TurnResult:
        round_no = self.chore_round
        chore = self._chore_for(round_no)
        doubled = round_no in self.script["doubles"]
        steal_power = self.script["powers"].get(round_no)

        winner.score += 1
        self.assignments[loser.player_id].append({
            "chore": chore["id"],
            "count": 2 if doubled else 1,
            "round": round_no,
            "double": doubled,
            "source": "round",
        })

        parts = [f"{winner.name} wins with {high} to {low} - {loser.name} does the {chore['label'].lower()}"]
        if doubled:
            parts.append("DOUBLE TROUBLE - twice!")
        steal = None
        if steal_power:
            steal = self._apply_steal(steal_power, winner, loser)
            parts.append(steal["message"])

        self.results.append({
            "round": round_no,
            "chore": chore["id"],
            "winner_id": winner.player_id,
            "loser_id": loser.player_id,
            "high": high,
            "low": low,
            "double": doubled,
            "steal": steal["power"] if steal else None,
            "lucky": bool(self._lucky(round_no)),
        })
        self._sync_stats()

        self.chore_round += 1
        if self.chore_round > self.rounds:
            return self._finish(" ".join(parts))
        return TurnResult(
            message=" ".join(parts),
            highlight="big" if (doubled or steal) else "good",
        )

    def _apply_steal(self, power: str, winner: PlayerState, loser: PlayerState) -> dict:
        """Resolve the winner's steal power against the chores on the table.

        The printed rules let the winner choose. There is no way to ask them
        mid-throw, so each power resolves against the one obvious target - the
        chore just assigned, or the winner's most recent - and is announced.
        A power with nothing to work on falls back to doubling, which always
        has something: the chore that was assigned a moment ago.
        """
        mine = self.assignments[winner.player_id]
        theirs = self.assignments[loser.player_id]

        if power == "swap" and mine:
            given = mine.pop()
            given_chore = CHORE_BY_ID[given["chore"]]
            theirs.append({**given, "source": "steal"})
            return {"power": "swap",
                    "message": f"STEAL ROUND! {winner.name} hands the {given_chore['label'].lower()} to {loser.name}."}

        if power == "remove" and mine:
            dropped = mine.pop()
            dropped_chore = CHORE_BY_ID[dropped["chore"]]
            return {"power": "remove",
                    "message": f"STEAL ROUND! {winner.name} wipes the {dropped_chore['label'].lower()} off their list."}

        latest = theirs[-1]
        latest["count"] += 1
        latest["double"] = True
        latest["source"] = "steal"
        doubled_chore = CHORE_BY_ID[latest["chore"]]
        return {"power": "double",
                "message": f"STEAL ROUND! {winner.name} makes {loser.name} do the {doubled_chore['label'].lower()} again."}

    # ------------------------------------------------------------ the end

    def _finish(self, lead: str) -> TurnResult:
        """Spin the wheel, crown the champion, end the game."""
        # The wheel goes to whoever lost the most rounds; a dead heat on rounds
        # sends it to whoever ended up with the most chores.
        spinner = sorted(self.players, key=lambda p: (p.score, -self._chore_count(p)))[0]
        result = WHEEL[self.script["wheel_index"]]
        wheel_note = ""

        if result["kind"] == "jackpot":
            cleared = self._chore_count(spinner)
            self.assignments[spinner.player_id] = []
            wheel_note = (f"{spinner.name} spins NO CHORES and walks away with nothing to do"
                          if cleared else f"{spinner.name} spins NO CHORES")
        elif result["kind"] == "chore":
            self.assignments[spinner.player_id].append({
                "chore": None, "label": result["label"], "count": 1,
                "round": None, "double": False, "source": "wheel",
            })
            wheel_note = f"{spinner.name} spins {result['label'].upper()}"
        else:
            self.rewards[spinner.player_id].append(result["label"])
            wheel_note = f"{spinner.name} spins {result['label'].upper()}"

        self.wheel = {
            "index": self.script["wheel_index"],
            "key": result["key"],
            "label": result["label"],
            "kind": result["kind"],
            "player_id": spinner.player_id,
            "player": spinner.name,
        }
        self._sync_stats()

        # Champion: most rounds won, then fewest chores. Level on both is a
        # genuine draw rather than a coin toss decided by roster order.
        ranked = sorted(self.players, key=lambda p: (-p.score, self._chore_count(p)))
        best, rest = ranked[0], ranked[1]
        self.draw = best.score == rest.score and self._chore_count(best) == self._chore_count(rest)

        for player in ranked:
            self.finish_player(player)
        self.finished = True
        if self.draw:
            for player in self.players:
                player.place = 1
            self.winner_id = None
            crown = f"It's a DRAW at {best.score} rounds each!"
        else:
            self.winner_id = best.player_id
            crown = f"{best.name} is the HOUSEHOLD CHAMPION with {best.score} rounds!"

        return TurnResult(finished=True, cue="game.win", highlight="big",
                          message=f"{lead} {wheel_note}. {crown}")

    # ------------------------------------------------------------ UI

    def target_hint(self, player: PlayerState) -> str | None:
        if self.finished:
            return "Chore lists are final - photograph the screen before you clear it."
        thrower = self._thrower_for(len(self._throws), self._player(self._leader_id or player.player_id))
        lucky = self._lucky(self.chore_round)
        parts = [f"Round {self.chore_round} of {self.rounds}",
                 self.chore["label"].upper(),
                 f"{thrower.name} to throw - highest score dodges it"]
        if self.chore_round in self.script["doubles"]:
            parts.append("DOUBLE TROUBLE - the loser does it twice")
        if lucky:
            parts.append(f"Lucky Target on {lucky['segment']}")
        return " · ".join(parts)

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        lucky = self._lucky(self.chore_round)
        return [lucky["segment"]] if lucky and not self.finished else []

    def theme(self) -> str:
        return "classic"

    def view(self) -> dict:
        lucky = self._lucky(self.chore_round)
        # Whose dart is expected next - the bespoke layout and the phone both
        # show this rather than the engine's current player, which is only the
        # player who opened the round.
        leader = self._player(self._leader_id) if self._leader_id else self.players[0]
        throwing = self._thrower_for(len(self._throws), leader)
        return {
            "kind": "chores",
            "chore": {**self.chore, "round": self.chore_round},
            "rounds": self.rounds,
            "chore_round": min(self.chore_round, self.rounds),
            "double": self.chore_round in self.script["doubles"],
            "steal": self.chore_round in self.script["steals"],
            "lucky": lucky,
            "throwing_player_id": None if self.finished else throwing.player_id,
            "thrown": [{"player_id": pid, "score": score} for pid, score in self._throws],
            "results": self.results,
            "assignments": {
                pid: [self._assignment_view(entry) for entry in entries]
                for pid, entries in self.assignments.items()
            },
            "bonuses": {
                pid: [BONUS_BY_KEY[key] for key in keys] for pid, keys in self.bonuses.items()
            },
            "rewards": self.rewards,
            "wheel": self.wheel,
            "wheel_segments": WHEEL,
            "draw": self.draw,
            "library": CHORES,
        }

    @staticmethod
    def _assignment_view(entry: dict) -> dict:
        """A chore list entry the UI can render without looking anything up -
        wheel forfeits have no chore id, so they carry their own label."""
        chore = CHORE_BY_ID.get(entry.get("chore") or "")
        return {
            "id": entry.get("chore"),
            "label": chore["label"] if chore else entry.get("label", "Forfeit"),
            "emoji": chore["emoji"] if chore else "\U0001f3a1",
            "count": entry["count"],
            "double": entry["double"],
            "round": entry["round"],
            "source": entry["source"],
        }
