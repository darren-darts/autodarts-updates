"""The game catalogue: what the library screen shows, and how a game is built.

Every entry carries its own rules text and difficulty levels so the UI never
hard-codes game knowledge - adding a game here makes it appear in the library,
with its rules page and difficulty picker, automatically.

`builder` is None for games that are catalogued but not yet implemented; the
library shows those as "coming soon" rather than hiding them, so the intended
scope stays visible.

Rules summarised from darts501.com, dolfdarts.com and dartsy.org (see PLAN.md).
"""
from __future__ import annotations

from typing import Any, Callable

from .base import Game, PlayerState

_BUILDERS: dict[str, Callable[..., Game]] = {}


def register(slug: str):
    def wrap(cls):
        _BUILDERS[slug] = cls
        return cls
    return wrap


def _d(key: str, label: str, blurb: str, **opts) -> dict:
    return {"key": key, "label": label, "blurb": blurb, "options": opts}


CATALOGUE: list[dict[str, Any]] = [
    {
        "slug": "x01",
        "name": "X01",
        "tagline": "The classic. Race to exactly zero.",
        "category": "Classic",
        "min_players": 1,
        "max_players": 8,
        "art": "x01",
        "rules": [
            "Everyone starts on the same score (choose 201 through 701 before the game).",
            "Each turn you throw three darts and the total is subtracted from your score.",
            "You must finish on exactly zero, and the final dart must be a double.",
            "Going below zero, landing on exactly one, or finishing on the wrong dart is a BUST - the whole turn is wiped and your score goes back to what it was.",
            "First player to zero wins.",
        ],
        "difficulties": {
            "easy": _d("easy", "301 - Straight out", "Short game, any dart can finish. Great for a first go.", start=301, double_out=False),
            "normal": _d("normal", "501 - Double out", "The standard pub and tournament game.", start=501, double_out=True),
            "hard": _d("hard", "701 - Double in & out", "You must hit a double to start scoring, and a double to finish.", start=701, double_out=True, double_in=True),
        },
    },
    {
        "slug": "round-the-clock",
        "name": "Round the Clock",
        "tagline": "1 to 20 in order. Simple to learn, brutal to finish.",
        "category": "Practice",
        "min_players": 1,
        "max_players": 8,
        "art": "clock",
        "rules": [
            "Work your way around the board from 1 to 20 in order, finishing on the bullseye.",
            "Hit your current target and you move on to the next number - within the same turn if you have darts left.",
            "Any part of the number counts on the easier levels.",
            "First player to get past 20 and hit the bull wins.",
        ],
        "difficulties": {
            "easy": _d("easy", "Any hit counts", "Singles, doubles or trebles all advance you.", require="any"),
            "normal": _d("normal", "Doubles jump ahead", "A double moves you on two numbers, a treble three.", require="any", bonus=True),
            "hard": _d("hard", "Doubles only", "You must hit the DOUBLE of each number to advance.", require="double"),
        },
    },
    {
        "slug": "shanghai",
        "name": "Shanghai",
        "tagline": "One number per round - and one instant-win throw.",
        "category": "Classic",
        "min_players": 1,
        "max_players": 8,
        "art": "shanghai",
        "rules": [
            "Round 1 everyone throws at the 1, round 2 at the 2, and so on.",
            "Only the round's number scores. A single scores its face value, a double twice, a treble three times.",
            "Hit a single, a double AND a treble of the number in the same turn - a 'Shanghai' - and you win instantly.",
            "Highest total when the last round finishes wins.",
        ],
        "difficulties": {
            "easy": _d("easy", "Rounds 1-7", "A quick game over the low numbers.", rounds=7),
            "normal": _d("normal", "Rounds 1-20", "The full trip around the board.", rounds=20),
            "hard": _d("hard", "Rounds 10-20", "Only the big numbers, where the trebles hurt.", rounds=20, start_round=10),
        },
    },
    {
        "slug": "killer",
        "name": "Killer",
        "tagline": "Earn marks on your slices, then eliminate your rivals.",
        "category": "Party",
        "min_players": 2,
        "max_players": 12,
        "art": "killer",
        "rules": [
            "Targets are assigned automatically when the game starts - each player owns a group of adjacent slices.",
            "Hit any of your highlighted slices three times to become a Killer. Doubles and trebles count as multiple hits.",
            "Once you're a Killer, hitting a highlighted opponent slice takes their lives - one per hit, more for doubles and trebles.",
            "Lose all three lives and you're out. The last player left alive wins the game.",
        ],
        "difficulties": {
            "easy": _d("easy", "Easy", "3 slices · up to 6 players"),
            "normal": _d("normal", "Medium", "2 slices · up to 10 players"),
            "hard": _d("hard", "Hard", "1 slice · up to 12 players"),
        },
    },
    {
        "slug": "donkey-derby",
        "name": "Donkey Derby",
        "tagline": "Race forward on your number - knock rivals back on theirs.",
        "category": "Party",
        "min_players": 2,
        "max_players": 8,
        "art": "derby",
        "rules": [
            "Every player gets a donkey and an automatically assigned target number.",
            "Hit your own number to move your donkey forward; hit a rival's number to push theirs backward.",
            "A single moves one step, a double two and a treble three.",
            "Other numbers, the bull and misses do not move a donkey.",
            "The first donkey to reach the finish post wins the race.",
        ],
        "difficulties": {
            "easy": _d("easy", "Short course - 8 steps", "A quick sprint.", track=8),
            "normal": _d("normal", "Classic - 12 steps", "A proper race.", track=12),
            "hard": _d("hard", "Long course - 20 steps", "A real stayer's race.", track=20),
        },
    },
    {
        "slug": "space-invaders",
        "name": "Space Invaders",
        "tagline": "Defend orbit - destroy the alien fleet lane by lane.",
        "category": "Arcade",
        "min_players": 1,
        "max_players": 6,
        "art": "invaders",
        "rules": [
            "An alien fleet orbits the board in three rows - hit a numbered sector to fire into that lane.",
            "Doubles and trebles fire two or three shots down the lane.",
            "The outer bull arms the Multi-Cannon: your next numbered hit also fires the lanes left and right.",
            "The inner bull fires one shot across every numbered lane.",
            "Aliens advance after each round. Three advances breach the grid and cost one of three defence lives.",
            "Clear the fleet before all three defence lives are lost. Highest score wins.",
        ],
        "difficulties": {
            "easy": _d("easy", "Easy", "20 aliens"),
            "normal": _d("normal", "Medium", "31 aliens"),
            "hard": _d("hard", "Hard", "45 aliens"),
        },
    },
    {
        "slug": "mr-vs-mrs",
        "name": "Mr vs Mrs: Chore Challenge",
        "tagline": "Throw for the washing up. Loser does it.",
        "category": "Party",
        "min_players": 2,
        "max_players": 2,
        "art": "chores",
        "rules": [
            "A household chore appears on screen and both players throw ONE dart at it.",
            "Highest score wins the round and avoids the chore - the other player gets it for the week.",
            "Level scores are sudden death: both throw again for the same chore until someone wins it.",
            "Some rounds are DOUBLE TROUBLE - the loser does that chore twice.",
            "A Lucky Target lights up a number on the board. Hit it and you win a bonus - a Pass Token, a Swap, Partner's Choice or a Mystery Reward - which you hold and settle between yourselves.",
            "A Steal Round hands the winner a power: pass one of their own chores over, wipe one off their list, or make their partner do theirs twice.",
            "After the last round the player who lost the most rounds spins the Wheel of Misfortune.",
            "Most rounds won is crowned Household Champion. The chore lists stay on screen at the end - photograph them for the week.",
        ],
        "difficulties": {
            "easy": _d("easy", "5 rounds", "A quick settle-it-now game.", rounds=5),
            "normal": _d("normal", "10 rounds", "The standard week's chores.", rounds=10),
            "hard": _d("hard", "15 rounds", "The full house - chores start repeating.", rounds=15),
        },
    },
    {
        "slug": "snakes-and-ladders",
        "name": "Snakes & Ladders",
        "tagline": "Three darts, three moves - up the ladders, down the snakes, first to 100.",
        "category": "Party",
        "min_players": 2,
        "max_players": 4,
        "art": "snakes",
        "rules": [
            "Take turns throwing three darts - each dart moves your token on its own.",
            "A dart's score becomes squares to move: roughly its score divided by five, so a treble 20 is 12.",
            "Land on the foot of a ladder and climb straight up; land on a snake's head and slide straight down.",
            "That climb or slide happens before your next dart, which is thrown from the new square.",
            "You must land exactly on 100 - a dart that would overshoot doesn't move you, but is still used.",
            "First token to reach square 100 wins the race.",
        ],
        "difficulties": {
            "easy": _d("easy", "Big hops", "Each dart moves score ÷ 4 - a quicker race.", divisor=4),
            "normal": _d("normal", "Classic", "Each dart moves score ÷ 5.", divisor=5),
            "hard": _d("hard", "Long game", "Each dart moves score ÷ 6 - more turns, more snakes.", divisor=6),
        },
    },
    # ---------------------------------------------------------------- planned
    {"slug": "cricket", "name": "Cricket", "tagline": "Close 15-20 and the bull before your opponent.", "category": "Classic",
     "min_players": 2, "max_players": 4, "art": "cricket", "builder": None,
     "rules": ["Close each of 20, 19, 18, 17, 16, 15 and the bull by hitting them three times.",
               "Once you've closed a number you score on it until everyone else closes it too.",
               "Highest score with everything closed wins."],
     "difficulties": {"easy": _d("easy", "No points", "Pure race to close everything."),
                      "normal": _d("normal", "Standard", "Close and score."),
                      "hard": _d("hard", "Cut-throat", "Points you score are given to opponents instead.")}},
    {"slug": "halve-it", "name": "Halve It", "tagline": "Miss the target and lose half your score.", "category": "Classic",
     "min_players": 1, "max_players": 8, "art": "halveit", "builder": None,
     "rules": ["Each round has a set target - a number, a double, a treble or the bull.",
               "Hit it and add the score. Miss with all three darts and your total is halved.",
               "Highest score at the end wins."],
     "difficulties": {"easy": _d("easy", "Friendly", "Gentle target list."),
                      "normal": _d("normal", "Standard", "The classic target list."),
                      "hard": _d("hard", "Trebles & bull", "Only the hardest targets.")}},
    {"slug": "golf", "name": "Darts Golf", "tagline": "18 holes, lowest score wins.", "category": "Practice",
     "min_players": 1, "max_players": 6, "art": "golf",
     "rules": ["Numbers 1 to 18 are the holes, played in order - everyone plays a hole, then the course moves on.",
               "You get three darts at each hole, but the first one to hit it finishes the hole.",
               "Treble = hole in one (1 stroke), double = birdie (2), single = par (3).",
               "Miss the hole with all three darts and you take a bogey (5).",
               "Lowest total strokes at the end of the course wins."],
     "difficulties": {"easy": _d("easy", "9 holes", "A quick round over the low numbers.", holes=9),
                      "normal": _d("normal", "18 holes", "The full course.", holes=18),
                      "hard": _d("hard", "18 holes, championship", "A single only gets you 4, and a missed hole costs 6.",
                                 holes=18, strict=True)}},
    {"slug": "baseball", "name": "Baseball", "tagline": "Nine innings, nine numbers.", "category": "Arcade",
     "min_players": 1, "max_players": 8, "art": "baseball", "builder": None,
     "rules": ["Innings 1-9 use numbers 1-9. Only that inning's number scores.",
               "Single = 1 run, double = 2 runs, treble = 3 runs.",
               "Most runs after nine innings wins."],
     "difficulties": {"easy": _d("easy", "5 innings", "A short game."),
                      "normal": _d("normal", "9 innings", "The full game."),
                      "hard": _d("hard", "Extra innings", "9 innings, ties play on.")}},
    {"slug": "chase-the-dragon", "name": "Chase the Dragon", "tagline": "Trebles 10 to 20, then both bulls.", "category": "Practice",
     "min_players": 1, "max_players": 6, "art": "dragon", "builder": None,
     "rules": ["Hit the treble of 10, then 11, and so on up to 20.", "Finish with the outer bull then the bullseye.",
               "First to complete the chase wins."],
     "difficulties": {"easy": _d("easy", "Any hit", "Any part of the number advances you."),
                      "normal": _d("normal", "Trebles", "Trebles only, as intended."),
                      "hard": _d("hard", "Trebles, no mercy", "Miss the whole turn and drop back a number.")}},
    {"slug": "nine-lives", "name": "Nine Lives", "tagline": "1 to 20 in order - miss and lose a life.", "category": "Practice",
     "min_players": 1, "max_players": 8, "art": "lives", "builder": None,
     "rules": ["Work from 1 to 20 in order.", "Miss the target with all three darts and you lose a life.",
               "Lose all your lives and you're out. Last player standing wins."],
     "difficulties": {"easy": _d("easy", "9 lives", "Room for mistakes."),
                      "normal": _d("normal", "5 lives", "Standard."),
                      "hard": _d("hard", "3 lives", "Very unforgiving.")}},
    {"slug": "high-score", "name": "High Score", "tagline": "Ten turns. Biggest total wins.", "category": "Practice",
     "min_players": 1, "max_players": 8, "art": "highscore", "builder": None,
     "rules": ["Simply score as much as you can.", "Everything on the board counts.",
               "Highest total after the set number of turns wins."],
     "difficulties": {"easy": _d("easy", "5 turns", "Quick blast."),
                      "normal": _d("normal", "10 turns", "Standard."),
                      "hard": _d("hard", "10 turns, doubles only", "Only doubles score.")}},
    {"slug": "sudden-death", "name": "Sudden Death", "tagline": "Lowest score each round is out.", "category": "Party",
     "min_players": 3, "max_players": 12, "art": "suddendeath", "builder": None,
     "rules": ["Everyone throws three darts.", "The lowest scorer that round is eliminated on the spot.",
               "Keep going until one player remains."],
     "difficulties": {"easy": _d("easy", "Warm-up round", "First round is safe."),
                      "normal": _d("normal", "Standard", "Straight in."),
                      "hard": _d("hard", "Bottom two", "The two lowest go each round.")}},
    {"slug": "noughts-and-crosses", "name": "Noughts & Crosses", "tagline": "Claim three in a row on the board.", "category": "Party",
     "min_players": 2, "max_players": 2, "art": "tictactoe",
     "rules": ["Nine board numbers form a 3x3 grid, with the bullseye in the centre square.",
               "Player 1 is X, player 2 is O. Hit a square's number to claim it - once claimed, a square is locked.",
               "Hitting a number that's not in the grid, or a square somebody already owns, just costs you the dart.",
               "First to own three squares in a row - across, down or diagonally - wins.",
               "If all nine squares fill with no line, it's a draw."],
     "difficulties": {"easy": _d("easy", "Easy", "Any ring claims a square, and you can claim several in one turn.",
                                 claim="any", end_turn_after_claim=False, inner_bull_only=False),
                      "normal": _d("normal", "Standard", "Any ring claims, but a claim ends your turn - one square per visit.",
                                   claim="any", end_turn_after_claim=True, inner_bull_only=False),
                      "hard": _d("hard", "Hard", "Only doubles and trebles claim, and the centre needs the inner bull.",
                                 claim="double_or_treble", end_turn_after_claim=False, inner_bull_only=True)}},
    {"slug": "gotcha", "name": "Gotcha", "tagline": "Count up to 301 - and reset anyone you match.", "category": "Party",
     "min_players": 2, "max_players": 8, "art": "gotcha", "builder": None,
     "rules": ["Score UP from zero towards the target.", "Land on another player's exact score and they go back to zero.",
               "You must finish on exactly the target."],
     "difficulties": {"easy": _d("easy", "Target 181", "Short game."),
                      "normal": _d("normal", "Target 301", "Standard."),
                      "hard": _d("hard", "Target 501", "Long and cruel.")}},
    {"slug": "football", "name": "Darts Football", "tagline": "Win the bull, then score doubles.", "category": "Arcade",
     "min_players": 2, "max_players": 6, "art": "football", "builder": None,
     "rules": ["Hit the bullseye to take possession.", "While you have possession every double is a goal.",
               "Lose possession when an opponent hits the bull. First to the goal target wins."],
     "difficulties": {"easy": _d("easy", "3 goals", "Quick match."),
                      "normal": _d("normal", "5 goals", "Standard."),
                      "hard": _d("hard", "10 goals", "Full ninety minutes.")}},
    {"slug": "bermuda-triangle", "name": "Bermuda Triangle", "tagline": "Hit the target or watch your score sink.", "category": "Practice",
     "min_players": 1, "max_players": 8, "art": "bermuda", "builder": None,
     "rules": ["A fixed list of targets: numbers, doubles, trebles and the bull.",
               "Hit the target to add its value. Miss entirely and your score halves.",
               "Highest score at the end wins."],
     "difficulties": {"easy": _d("easy", "Short list", "Fewer targets."),
                      "normal": _d("normal", "Standard", "The classic list."),
                      "hard": _d("hard", "Full list", "Every target, no mercy.")}},
    {"slug": "follow-the-leader", "name": "Follow the Leader", "tagline": "Hit what the last player hit - or drop a life.", "category": "Party",
     "min_players": 2, "max_players": 8, "art": "leader", "builder": None,
     "rules": ["The leader names a target by hitting it.", "Everyone else must hit the same target.",
               "Fail and you lose a life. Last player standing wins."],
     "difficulties": {"easy": _d("easy", "5 lives", "Forgiving."),
                      "normal": _d("normal", "3 lives", "Standard."),
                      "hard": _d("hard", "1 life", "One mistake and you're gone.")}},
    {"slug": "scram", "name": "Scram", "tagline": "One blocks, one scores. Then swap.", "category": "Party",
     "min_players": 2, "max_players": 2, "art": "scram", "builder": None,
     "rules": ["The blocker closes numbers by hitting them - closed numbers can no longer be scored.",
               "The scorer racks up as many points as possible before everything is closed.",
               "Swap roles. Highest score wins."],
     "difficulties": {"easy": _d("easy", "Numbers 15-20", "A small board to fight over."),
                      "normal": _d("normal", "All numbers", "Standard."),
                      "hard": _d("hard", "Doubles to block", "The blocker must hit doubles.")}},
]

BY_SLUG = {entry["slug"]: entry for entry in CATALOGUE}


def get_definition(slug: str | None) -> dict | None:
    return BY_SLUG.get(slug or "")


def catalogue_view() -> list[dict]:
    """The library listing. `available` distinguishes playable games from
    catalogued ones so the UI can show what's coming without pretending
    it's ready."""
    out = []
    for entry in CATALOGUE:
        out.append({
            "slug": entry["slug"],
            "name": entry["name"],
            "tagline": entry["tagline"],
            "category": entry["category"],
            "art": entry["art"],
            "min_players": entry["min_players"],
            "max_players": entry["max_players"],
            "rules": entry["rules"],
            "available": entry["slug"] in _BUILDERS,
            "difficulties": [
                {"key": d["key"], "label": d["label"], "blurb": d["blurb"]}
                for d in entry["difficulties"].values()
            ],
        })
    return out


def build_game(slug: str, players: list[PlayerState], difficulty: str, options: dict | None = None) -> Game:
    definition = get_definition(slug)
    if definition is None:
        raise ValueError(f"unknown game {slug!r}")
    builder = _BUILDERS.get(slug)
    if builder is None:
        raise ValueError(f"{definition['name']} is not playable yet")
    level = definition["difficulties"][difficulty]
    merged = {**level["options"], **(options or {})}
    return builder(players, difficulty, merged)
