# Adding a game to the library

This is the complete instruction set for adding a new dart game to this
project, or modifying an existing one. It is written to be handed to another
engineer or AI with no other context.

**The headline: a new game is one Python file plus one catalogue entry. You do
not need to write any frontend code, any graphics, or any CSS.** The UI builds
itself from what the game reports. Section 5 explains exactly what you get for
free and what it looks like — read it before deciding you need a custom view,
because you almost certainly do not.

---

## 1. The short version

To add a game called "Darts Golf":

1. **Write the game class** in a file under `backend/games/` (a new file, or add
   to an existing themed one). Subclass `Game`, implement `apply_dart()`,
   decorate with `@register("golf")`.
2. **Add or complete the catalogue entry** in `backend/games/registry.py` —
   name, tagline, rules text, difficulties. Many games are *already
   catalogued* with `"builder": None` (see the appendix); for those you only
   need to delete that key and write the class.
3. **Import the module** in `backend/games/__init__.py` so the `@register`
   decorator actually runs.
4. **Verify** with the commands in section 9.

Nothing else. No route, no UI file, no build step.

---

## 2. How the engine works, and what you must not do

`MatchEngine` (`backend/games/engine.py`) owns everything about *running* a
match. A game only decides **what a dart means**.

The engine already handles, for every game:

| Handled for you | Where |
|---|---|
| Whose turn it is, and rotation past finished players | `_advance_turn_locked` |
| Counting darts, ending the turn at `darts_per_turn` | `_apply_dart_locked` |
| Waiting for the darts to be pulled out before the next player | `awaiting_takeout` |
| Undo, override a dart, previous player | `undo_dart`, `previous_turn` |
| Live push to every screen and phone | `_broadcast` over WebSocket |
| LED cues and sounds | `_flash`, `TurnResult.cue`, `TurnResult.highlight` |
| Manual dart entry when detection misses one | `POST /api/games/dart` |

### The rules you must follow

**1. Never drive the turn yourself.** Do not call `next_turn`, do not touch
`turn_index`, do not append to `darts_this_turn`. Return a `TurnResult` and let
the engine act on it. `end_turn=True` is how you stop a turn early.

**2. Your game must be deterministic.** Undo and "previous player" work by
*rebuilding the match from scratch* and replaying the action log through a
brand-new instance of your class (`_replay_locked`). If `apply_dart` consults
the clock, a global counter, or unseeded randomness, an undo will silently
produce a different game.

If you need randomness, use the established seed pattern — the seed is stored
in the options dict, which is handed to the rebuilt instance:

```python
seed = self.options.setdefault("seed", random.randrange(1 << 30))
self._rng = random.Random(seed)
```

`options.setdefault` is doing the real work: the first construction writes the
seed in, every rebuild reads the same one back out. Use `self._rng`, never the
module-level `random.*` functions.

**3. Do not use `self.round` for your own round counter.** The engine owns that
attribute and increments it every time turn order wraps to the first player.
This has already caused a real bug: Shanghai used `self.round` for its target
number, both it and the engine incremented it, and the target jumped two per
round — half the board was never thrown at. Use a differently-named attribute
(Shanghai now uses `self.target_round`).

**4. `apply_dart` is called once per dart, in order, and may be replayed.** Keep
per-turn scratch state in an instance attribute reset from `on_turn_start` (and
defensively on `dart_index == 0`, as Shanghai does — `on_turn_start` is not
called during a replay of a partial turn).

---

## 3. API reference

All of this is in `backend/games/base.py`.

### `Dart` — what you are given (frozen dataclass)

```python
dart.segment      # int | None   1-20, 25 for either bull, None if off-board
dart.multiplier   # int          0 = miss/out, 1 = single, 2 = double, 3 = treble
dart.score        # int          already resolved: segment * multiplier
dart.label        # str          "T20", "D16", "BULL", "25", "OUT", "MISS"
dart.x_mm         # float | None real landing position, None for manual entry
dart.y_mm         # float | None

dart.is_bull          # inner bull (50) — segment 25 AND multiplier 2
dart.is_outer_bull    # outer bull (25) — segment 25 AND multiplier 1
dart.is_miss          # multiplier == 0 or segment is None
dart.hits(number)     # landed in that number's bed, any ring
```

**The bull is segment 25.** Inner bull is `multiplier == 2` (score 50), outer
bull is `multiplier == 1` (score 25). `dart.hits(25)` is true for both. A dart
that hit the board outside the scoring area has `multiplier == 0` and
`label == "OUT"` — a real thrown dart worth nothing, not a detection failure.

### `TurnResult` — what you return

```python
TurnResult(
    end_turn=False,     # bool  stop the turn now (bust, life lost, eliminated)
    message=None,       # str   one short line shown to the player, e.g. "BUST!"
    cue=None,           # str   LED cue name — see section 6 for the valid list
    finished=False,     # bool  the whole game is over
    highlight=None,     # str   "good" | "bad" | "big" — UI emphasis and sound
)
```

Return a bare `TurnResult()` for "nothing happened" (a dart that does not
count). That is the common case and is completely fine.

`highlight="bad"` triggers a distinct sound on the play screen. `highlight="big"`
is for genuine moments — a win, becoming a Killer.

### `PlayerState` — per-player state

```python
player.player_id  # str, stable
player.name       # str
player.avatar     # str | None
player.score      # int  — shown large on every screen. See the note below.
player.stats      # dict — free-form, reaches the UI verbatim in the player list
player.finished   # bool
player.place      # int | None, 1 = winner
```

**`player.score` is whatever your game wants it to be.** It is simply "the big
number next to the player". Round the Clock stores the player's *current
target* in it; Killer stores *lives remaining*; X01 stores the remaining score.
Set it in `__init__` and keep it meaningful, because it is what the scoreboards
show. Put everything else in `player.stats`.

### `Game` — the hooks

```python
class MyGame(Game):
    slug = "my-game"          # must match the @register slug and the catalogue
    name = "My Game"
    darts_per_turn = 3        # override if your game is different

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        # self.options is the difficulty's options merged with any passed in
        # self.players, self.difficulty, self.finished, self.winner_id are set
        # Initialise player.score and player.stats for every player HERE.

    # REQUIRED
    def apply_dart(self, player, dart, dart_index) -> TurnResult:
        """dart_index is 0-based within the turn."""

    # All optional:
    def on_turn_start(self, player) -> TurnResult | None: ...
    def on_turn_end(self, player, darts: list[Dart]) -> TurnResult | None: ...
    def target_hint(self, player) -> str | None: ...       # big text hint
    def highlight_numbers(self, player) -> list[int]: ...  # light up board beds
    def theme(self) -> str: ...                            # board palette
    def view(self) -> dict: ...                            # your state, to the UI
```

Inherited helpers you should use rather than reimplement:

```python
self.active_players()        # players not yet finished
self.finish_player(player)   # finishing FIRST is winning — places count up 1,2,3
self.eliminate(player)       # being out is LOSING — places fill from the bottom,
                             # last survivor gets place 1 and wins
```

Use `finish_player` for race games (X01, Round the Clock). Use `eliminate` for
last-man-standing games (Killer, Nine Lives). Both set `self.finished` and
`self.winner_id` automatically when only one player is left.

---

## 4. The catalogue entry

In `backend/games/registry.py`, in the `CATALOGUE` list. Every field is
required unless noted:

```python
{
    "slug": "golf",              # unique, matches @register and Game.slug
    "name": "Darts Golf",
    "tagline": "18 holes, lowest score wins.",
    "category": "Practice",      # Classic | Practice | Party | Arcade
    "min_players": 1,
    "max_players": 6,
    "art": "golf",               # library artwork key — see section 5.4
    "rules": [                   # shown on the library page AND in-game help
        "Numbers 1-18 are the holes, played in order.",
        "Treble = hole in one (1), double = birdie (2), single = par (3), miss = bogey (5).",
        "Lowest total after 18 holes wins.",
    ],
    "difficulties": {
        "easy":   _d("easy",   "9 holes", "A quick round.", holes=9),
        "normal": _d("normal", "18 holes", "The full course.", holes=18),
        "hard":   _d("hard",   "18 holes, strict", "Only the outer single is par.",
                     holes=18, strict=True),
    },
    # "builder": None,   <-- ONLY for a catalogued but unplayable game. Remove
    #                        this key once you write the class.
},
```

`_d(key, label, blurb, **opts)` builds a difficulty. Everything after `blurb`
becomes keyword options merged into `self.options` for that difficulty. The
three keys `easy` / `normal` / `hard` are what the UI expects; the labels are
free text ("301 - Straight out", "Rounds 1-20").

Rules text is the player-facing explanation. Write it for someone holding a
dart, not for a programmer. Look at the existing entries for tone.

---

## 5. What the UI gives you for free

**This is the section that matters most.** No frontend work is required, and
no graphics need generating. Here is exactly what a brand-new game gets.

### 5.1 The play screen

`frontend/src/views/PlayView.vue` has three layouts. It picks by
`game.view()["kind"]`:

- `kind == "killer"` → the bespoke Killer arena
- `kind == "invaders"` → the bespoke Space Invaders arena
- **anything else → the general layout**, which is what your new game gets

The general layout renders, with no work from you:

- **A big score block** for the current player, labelled `SCORE`, showing
  `player.score`
- **Dart pips** for the turn, filling in with each dart's label
- **The live dartboard**, full size, in your chosen theme, with every number
  from `highlight_numbers()` lit up on the actual bed
- **The player list** down the side with names, avatars and scores
- **A rules panel** showing the game name and your `target_hint()`
- **The full control set** — Override/miss, Record complete miss, Undo dart,
  Previous player, Darts removed/Next player, and the help dialog with your
  `rules` text

The lit beds are the important part. `highlight_numbers()` returning `[13]`
lights the 13 on the board itself, so the player at the oche sees what to aim
at without reading anything. Return `[25]` for the bull. Return `[]` if your
game has no single target.

### 5.2 The phone screen

`PhoneGameControls.vue` also builds itself: current player, score with a label,
dart slots, your `message`, your `target_hint`, the scoreboard, and the full
correction controls. It labels the score `REMAINING` for `kind == "x01"`,
`LIVES` for `killer`, `POINTS` for `invaders`, and **`SCORE` for everything
else** — including yours.

### 5.3 Themes

`theme()` returns one of four palettes for the live board. These already exist
in `DartboardFace.vue`; you are picking one, not making one:

| `theme()` | Look |
|---|---|
| `"classic"` (default) | Standard black/cream board |
| `"killer"` | Purple/magenta |
| `"space"` | Blue/cyan |
| `"derby"` | Green/gold/parchment |

Anything else falls back to classic styling. Adding a genuinely new palette
means editing `DartboardFace.vue` CSS — avoid it unless asked.

### 5.4 Library artwork

The `art` key selects an inline SVG in `frontend/src/components/GameArt.vue`.
Existing keys: `x01`, `clock`, `shanghai`, `killer`, `derby`, `invaders`.

**Any unrecognised key falls through to a generic dartboard graphic**, which
looks fine. So set `"art": "golf"` and it will simply use the fallback. Do not
block on artwork.

### 5.5 `view()` — your own state

Whatever dict `view()` returns arrives at the UI as `state.game`. Always
include a `kind` key. For the general layout the UI does not read anything
else, so `view()` can be minimal:

```python
def view(self) -> dict:
    return {"kind": "golf", "hole": self.hole, "holes": self.holes}
```

Include your state anyway — it costs nothing, it shows up in the API, and it is
what a bespoke view would later read.

---

## 6. LED cues and sounds

`TurnResult.cue` fires an LED cue. **Only these names exist** (they come from
settings; an undefined name is silently ignored):

```
game.start   turn.start   throw.detected   bullseye
score.180    bust         game.win         takeout
idle         startup      calibration.start / .point / .done
```

For a game, in practice: `"game.win"` when someone wins, `"bust"` for a bad
outcome, `"bullseye"` for a big positive moment, `"score.180"` for an
exceptional score. Durations are set centrally in `engine.CUE_SECONDS`.

If you do not set a cue, the engine still fires `bullseye` automatically for
any dart where `dart.is_bull`. A game's own cue takes precedence.

Sounds are driven by `highlight`, not by cue — `"bad"` plays the review sound.

---

## 7. Worked example

A complete, working game: Darts Golf. **This one is already implemented and
shipped** — the finished article is `backend/games/golf.py`, and it is worth
reading in full alongside this. The version below is trimmed slightly (the real
one also implements the championship difficulty and per-player card stats) so
the shape stays visible.

Follow this pattern for one of the games in the appendix; do not overwrite
`golf.py` itself.

**Step 1** — `backend/games/golf.py`:

```python
"""Darts Golf - 18 holes, lowest score wins."""
from __future__ import annotations

from .base import Dart, Game, PlayerState, TurnResult
from .registry import register

# Strokes per outcome. Golf scoring is inverted: low is good.
HOLE_IN_ONE, BIRDIE, PAR, BOGEY = 1, 2, 3, 5


@register("golf")
class Golf(Game):
    slug = "golf"
    name = "Darts Golf"

    def __init__(self, players, difficulty, options=None):
        super().__init__(players, difficulty, options)
        self.holes = int(self.options.get("holes", 18))
        self.strict = bool(self.options.get("strict", False))
        # NOT self.round - the engine owns that. See section 2, rule 3.
        self.hole = 1
        for player in self.players:
            player.score = 0                       # strokes so far, low is good
            player.stats = {"holes": [], "best": None}
        self._scored_this_turn = False

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
        return PAR

    def apply_dart(self, player: PlayerState, dart: Dart, dart_index: int) -> TurnResult:
        if dart_index == 0:
            self._scored_this_turn = False        # replay-safe reset
        if self._scored_this_turn:
            return TurnResult()                   # hole already played out

        strokes = self._strokes(dart)
        if strokes is None:
            return TurnResult()

        self._scored_this_turn = True
        player.score += strokes
        player.stats["holes"].append(strokes)
        name = {HOLE_IN_ONE: "HOLE IN ONE!", BIRDIE: "Birdie", PAR: "Par"}[strokes]
        return TurnResult(
            end_turn=True,                        # hole played, move on
            message=f"{name} on hole {self.hole} (+{strokes})",
            cue="bullseye" if strokes == HOLE_IN_ONE else None,
            highlight="big" if strokes == HOLE_IN_ONE else "good",
        )

    def on_turn_end(self, player: PlayerState, darts: list[Dart]) -> TurnResult | None:
        if not self._scored_this_turn:            # never found the hole
            player.score += BOGEY
            player.stats["holes"].append(BOGEY)

        # The hole advances only after the last active player has played it.
        active = self.active_players()
        if not active or player is not active[-1]:
            return None
        if self.hole >= self.holes:
            best = min(self.players, key=lambda p: p.score)
            for p in sorted(self.players, key=lambda p: p.score):
                self.finish_player(p)
            self.finished = True
            self.winner_id = best.player_id
            return TurnResult(finished=True, cue="game.win", highlight="big",
                              message=f"{best.name} wins on {best.score} strokes!")
        self.hole += 1
        return None

    def target_hint(self, player: PlayerState) -> str | None:
        return f"Hole {self.hole} of {self.holes} - throw at {self.hole}"

    def highlight_numbers(self, player: PlayerState) -> list[int]:
        return [self.hole]

    def theme(self) -> str:
        return "derby"                            # green/gold suits golf

    def view(self) -> dict:
        return {"kind": "golf", "hole": self.hole, "holes": self.holes,
                "strict": self.strict,
                "cards": {p.player_id: p.stats["holes"] for p in self.players}}
```

**Step 2** — in `registry.py`, find your game's entry and delete its
`"builder": None,` key. Add the options your `__init__` reads to the
difficulties, and check the `rules` text still matches what the code actually
does — a difficulty whose blurb promises something the code does not implement
is worse than not having it:

```python
"difficulties": {"easy": _d("easy", "9 holes", "A quick round.", holes=9),
                 "normal": _d("normal", "18 holes", "The full course.", holes=18),
                 "hard": _d("hard", "18 holes, strict", "Only the outer single counts as par.",
                            holes=18, strict=True)},
```

**Step 3** — in `backend/games/__init__.py`:

```python
from . import golf, party, practice, x01  # noqa: F401  (imported for @register)
```

**Step 4** — verify (section 9). Done. It appears in the library, is playable
on the TV and the phone, lights the target bed on the live board, and has
working undo, override and previous-player.

Golf is also a useful reference for one awkward case: **its scoring is
inverted**. `player.score` counts strokes, so low wins. Nothing in the app
enforces "higher is better" — the scoreboards just display the number — so the
only thing that needed care was sorting ascending before calling
`finish_player`, so place 1 goes to the smallest card. If your game is a race
to the bottom, that is the whole trick.

---

## 8. Modifying an existing game

Same rules apply. Additionally:

- **Changing scoring** — the change is in `apply_dart`. Check whether the game
  keeps per-turn scratch state that also needs updating (X01 keeps
  `_turn_start_score` for busts).
- **Adding a difficulty** — add the key to `difficulties` in `registry.py` and
  read the new option in `__init__`. Give it a sensible default in
  `self.options.get(...)` so old saved games do not break.
- **Do not rename a slug.** It is the key for the builder registry, the
  catalogue and the frontend's `kind` checks.
- Killer and Space Invaders have bespoke frontend layouts keyed on their
  `kind`. Changing what their `view()` returns can break those layouts — check
  `PlayView.vue` for `kind === 'killer'` / `kind === 'invaders'` before
  removing a key from either.

---

## 9. Verification

Run all of these from the repo root. Nothing here needs the cameras or a real
board.

**1. It imports and registers:**

```bash
cd backend && ../backend/.venv/Scripts/python.exe -c "
import app
from games.registry import catalogue_view
for g in catalogue_view():
    if g['slug'] == 'golf': print(g['name'], 'available =', g['available'])
"
```

`available = True` means the builder registered. `False` means you forgot the
import in `__init__.py` or the slug does not match.

**2. It actually plays** — drive a real match through the HTTP API in-process,
which exercises the engine exactly as a live game does.

Note the `confirm-takeout` call. Once a turn ends — three darts, or your
`end_turn=True` — the engine sets `awaiting_takeout` and **rejects every
further dart** until the takeout is confirmed. A test that just fires darts in
a row silently throws most of them away and proves nothing.

```bash
cd backend && ../backend/.venv/Scripts/python.exe -c "
import app
from fastapi.testclient import TestClient
from players import store as pstore

c = TestClient(app.app)
ids = [p['id'] for p in pstore.list_players()][:2]
c.post('/api/games/start', json={'slug':'golf','difficulty':'easy','player_ids':ids})

def throw(seg, mult):
    s = c.post('/api/games/dart', json={'segment':seg,'multiplier':mult}).json()
    who = next(p['name'] for p in s['players'] if p['player_id'] == s['current_player_id'])
    print(f'  {seg}x{mult:<2} {who:8s} msg={s[\"message\"]!r}')
    if s['awaiting_takeout']:
        s = c.post('/api/games/confirm-takeout').json()
        print(f'          -> {[(p[\"name\"], p[\"score\"]) for p in s[\"players\"]]}')
    return s

throw(1, 3)                              # hole-in-one
throw(1, 2)                              # birdie
throw(9, 1); throw(9, 1); throw(9, 1)    # three misses -> bogey
c.post('/api/games/stop')
"
```

Read the output and check it against your own rules. For the Golf example
above this prints a hole-in-one (+1), a birdie (+2), the hole advancing only
after the last player has played it, and a bogey (+5) for a turn that never
found the target.

**3. Undo and previous-player are exact.** This is the test that catches
non-determinism, and it is the one most likely to fail:

```bash
cd backend && ../backend/.venv/Scripts/python.exe -c "
import app
from fastapi.testclient import TestClient
from players import store as pstore

c = TestClient(app.app)
ids = [p['id'] for p in pstore.list_players()][:2]
def state(): return c.get('/api/games/state').json()
def clear_turn():
    if state()['awaiting_takeout']: c.post('/api/games/confirm-takeout')

c.post('/api/games/start', json={'slug':'golf','difficulty':'easy','player_ids':ids})
for seg, mult in [(1,3),(1,2),(2,1)]:
    clear_turn(); c.post('/api/games/dart', json={'segment':seg,'multiplier':mult})

clear_turn()
before = state()
c.post('/api/games/dart', json={'segment':2,'multiplier':3})
assert state() != before, 'the probe dart was rejected - this test would prove nothing'
c.post('/api/games/undo')
after = state()

same = all(before[k] == after[k] for k in ('players','game','current_player_id','round'))
print('undo rebuilt the identical state:', same)
for k in ('players','game','current_player_id','round'):
    if before[k] != after[k]: print(f'  DIFFERS: {k}')
assert same, 'NON-DETERMINISTIC — see section 2 rule 2'
c.post('/api/games/stop')
"
```

The `assert state() != before` guard matters: if your game ends the turn on
that dart, or rejects it, the undo removes some *earlier* dart instead and the
comparison becomes meaningless. This version has been checked against a
deliberately broken game (an unseeded `random.randrange` in `__init__`) and
correctly reports `DIFFERS: game`.

**4. Nothing else broke:**

```bash
backend/.venv/Scripts/python.exe tools/detection_bench.py --selfcheck
backend/.venv/Scripts/python.exe tools/takeout_trace.py selfcheck
cd frontend && npm run build
```

The frontend build is only needed if you touched frontend files — a
backend-only game does not require it.

**5. Play it.** Restart the server (`uvicorn app:app --host 0.0.0.0 --port
8000` from `backend/`, no `--reload` is configured so a restart is required for
any Python change), open the library, and start the game. Use "Override / miss"
on the play screen to enter darts by hand without throwing.

---

## 10. If you really do need a custom layout

Only after section 5 has been ruled out. The pattern, following Killer:

1. `view()` returns a distinctive `kind`.
2. In `PlayView.vue`, add `<div v-else-if="kind === 'yourkind'">` before the
   final `v-else`, modelled on the existing `mode-killer` block.
3. Add a `<TakeoutPrompt>` and the shared action grid to it — every layout needs
   both. Copy them from an existing block.
4. Optionally add a `<g v-else-if="art === 'yourart'">` to `GameArt.vue`.

Be aware the three existing layouts were, until recently, byte-for-byte
duplicated for the takeout prompt and controls, which is why those are now
`TakeoutPrompt.vue` and `TurnCorrections.vue` components. Use the components;
do not paste a fourth copy.

---

## Appendix: games already catalogued and waiting for a class

These have full entries in `registry.py` with rules and difficulties written,
and `"builder": None`. Each needs only a class and the import — steps 1, 3, 4.
Difficulty options are described in the blurbs but **not yet defined as keyword
options**, so add those as you implement.

| Slug | Name | Category |
|---|---|---|
| `cricket` | Cricket | Classic |
| `halve-it` | Halve It | Classic |
| `baseball` | Baseball | Arcade |
| `chase-the-dragon` | Chase the Dragon | Practice |
| `nine-lives` | Nine Lives | Practice |
| `high-score` | High Score | Practice |
| `sudden-death` | Sudden Death | Party |
| `gotcha` | Gotcha | Party |
| `football` | Darts Football | Arcade |
| `bermuda-triangle` | Bermuda Triangle | Practice |
| `follow-the-leader` | Follow the Leader | Party |
| `scram` | Scram | Party |

Easiest first, if you want a running order: **High Score**, **Baseball**,
**Nine Lives** and **Chase the Dragon** are all single-target progressions very
close to the Round the Clock pattern in `practice.py`, and to the shipped
`golf.py`.
**Cricket** and **Scram** need per-number ownership state and are more
involved (`oxo.py`, now shipped, is the reference for that shape - fixed
target grid, ownership array, line checks). **Sudden Death** and **Follow the Leader** need
end-of-round comparisons across players — use `on_turn_end` with the
`player is active[-1]` check that Shanghai and the Golf example use.

## Reference files

| File | What it is |
|---|---|
| `backend/games/base.py` | The contract — `Dart`, `TurnResult`, `PlayerState`, `Game` |
| `backend/games/registry.py` | Catalogue and `@register` |
| `backend/games/engine.py` | `MatchEngine` — turn order, undo, replay. Do not edit for a new game |
| `backend/games/x01.py` | Example: busts, restoring turn-start state |
| `backend/games/practice.py` | Example: simplest possible games. **Start here** |
| `backend/games/party.py` | Example: seeded randomness, elimination, bespoke views |
| `frontend/src/views/PlayView.vue` | The three play layouts |
| `frontend/src/components/PhoneGameControls.vue` | The phone layout |
| `frontend/src/components/DartboardFace.vue` | Board rendering and the four themes |
| `frontend/src/components/GameArt.vue` | Library artwork by `art` key |
