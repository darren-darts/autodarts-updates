<script setup>
// A real golf scorecard: a row per player, a column per hole, colour-coded by
// how the hole was played, with the running total on the right.
//
// This is the one game where LOW wins, so the leaderboard sorts ascending and
// the leader is the smallest number - the opposite of every other scoreboard in
// the app. Worth stating loudly here because it is exactly the sort of thing
// that reads as a bug when it is actually the rules.
import { computed } from 'vue'

const props = defineProps({
  view: { type: Object, required: true },     // the golf game view
  players: { type: Array, default: () => [] },
  currentId: { type: String, default: null },
})

const holes = computed(() => props.view.holes ?? 18)
const hole = computed(() => props.view.hole ?? 1)
const cards = computed(() => props.view.cards ?? {})
const par = computed(() => props.view.par ?? 3)
const bogey = computed(() => props.view.bogey ?? 5)

// Ascending: fewest strokes leads. Players who have played fewer holes are not
// really "ahead", but everyone plays each hole before the course moves on, so
// the cards stay level and a plain sort is honest.
const standings = computed(() =>
  [...props.players].sort((a, b) => (a.score ?? 0) - (b.score ?? 0)),
)

const thru = computed(() => {
  const played = standings.value.map((p) => (cards.value[p.player_id] ?? []).length)
  return played.length ? Math.min(...played) : 0
})

function shotClass(strokes) {
  if (strokes == null) return 'empty'
  if (strokes === 1) return 'ace'
  if (strokes === 2) return 'birdie'
  if (strokes <= par.value) return 'par'
  if (strokes >= bogey.value) return 'bogey'
  return 'over'
}

function cardFor(id) {
  const played = cards.value[id] ?? []
  return Array.from({ length: holes.value }, (_, i) => played[i] ?? null)
}

// Split into nines, exactly as a paper card does. This is not only authentic:
// eighteen columns will not fit the side panel, and a horizontally scrolling
// scorecard on a television nobody is sitting near is useless.
const nines = computed(() => {
  const out = []
  for (let start = 0; start < holes.value; start += 9) {
    const numbers = []
    for (let h = start + 1; h <= Math.min(start + 9, holes.value); h += 1) numbers.push(h)
    out.push({
      label: out.length === 0 ? (holes.value > 9 ? 'OUT' : 'TOT') : out.length === 1 ? 'IN' : `${out.length + 1}`,
      numbers,
    })
  }
  return out
})

/** Strokes taken across just this stretch of holes. */
function subtotal(id, numbers) {
  const played = cards.value[id] ?? []
  return numbers.reduce((sum, h) => sum + (played[h - 1] ?? 0), 0)
}
</script>

<template>
  <div class="card-wrap">
    <div class="card-head">
      <span class="card-title">SCORECARD</span>
      <span class="card-thru">THRU {{ thru }} OF {{ holes }}</span>
    </div>

    <table v-for="nine in nines" :key="nine.label" class="card-table">
      <thead>
        <tr>
          <th class="who">PLAYER</th>
          <th
            v-for="h in nine.numbers"
            :key="h"
            class="hole"
            :class="{ now: h === hole, done: h < hole }"
          >{{ h }}</th>
          <th class="total">{{ nine.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in standings" :key="p.player_id" :class="{ current: p.player_id === currentId }">
          <td class="who">
            <img v-if="p.avatar" :src="p.avatar" alt="" />
            <span>{{ p.name }}</span>
          </td>
          <td
            v-for="h in nine.numbers"
            :key="h"
            class="shot"
            :class="[shotClass(cardFor(p.player_id)[h - 1]), { now: h === hole }]"
          >{{ cardFor(p.player_id)[h - 1] ?? '·' }}</td>
          <td class="total">{{ subtotal(p.player_id, nine.numbers) || '·' }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="nines.length > 1" class="card-grand">
      <span v-for="p in standings" :key="p.player_id" :class="{ current: p.player_id === currentId }">
        <em>{{ p.name }}</em><b>{{ p.score ?? 0 }}</b>
      </span>
    </div>

    <div class="card-key">
      <span class="ace">1 ACE</span>
      <span class="birdie">2 BIRDIE</span>
      <span class="par">{{ par }} PAR</span>
      <span class="bogey">{{ bogey }} BOGEY</span>
      <em>fewest strokes wins</em>
    </div>
  </div>
</template>

<style scoped>
.card-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.card-title {
  color: #f3e7c8;
  font-size: 11px;
  font-weight: 950;
  letter-spacing: 0.18em;
}

.card-thru {
  color: #9fc79b;
  font-size: 9px;
  font-weight: 900;
  letter-spacing: 0.12em;
}

.card-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 2px;
  font-family: ui-monospace, monospace;
}

.card-table th {
  padding: 2px 0;
  color: #86a882;
  font-size: 8px;
  font-weight: 900;
  letter-spacing: 0.06em;
}

.card-table th.hole {
  min-width: 17px;
  border-radius: 3px;
}

.card-table th.hole.done { color: #cfe3c9; }

.card-table th.hole.now {
  background: #f2c14e;
  color: #22300f;
}

.card-table th.who,
.card-table td.who {
  width: 1%;
  padding-right: 6px;
  text-align: left;
  white-space: nowrap;
}

td.who {
  display: flex;
  align-items: center;
  gap: 5px;
  color: #f0f6ea;
  font-family: inherit;
  font-size: 11px;
  font-weight: 800;
}

td.who img {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  object-fit: cover;
  border: 1px solid rgba(198, 224, 190, 0.4);
}

.shot {
  min-width: 17px;
  height: 20px;
  border-radius: 3px;
  background: rgba(16, 34, 14, 0.75);
  color: #7f9a7b;
  font-size: 10px;
  font-weight: 800;
  text-align: center;
}

.shot.ace    { background: #f2c14e; color: #2a1e05; box-shadow: 0 0 8px rgba(242, 193, 78, 0.6); }
.shot.birdie { background: #57c26b; color: #06210c; }
.shot.par    { background: #2f5f36; color: #dff3dd; }
.shot.over   { background: #7a5a22; color: #f6e6c4; }
.shot.bogey  { background: #a3423a; color: #ffe4e0; }
.shot.now    { outline: 2px solid #f2c14e; }

.total {
  min-width: 26px;
  padding-left: 4px;
  color: #f6f2e2;
  font-size: 13px;
  font-weight: 950;
  text-align: right;
}

tr.current td.who span { color: #f2c14e; }
tr.current .total { color: #f2c14e; }

.card-key {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  font-size: 8px;
  font-weight: 900;
  letter-spacing: 0.05em;
}

.card-key span {
  padding: 1px 5px;
  border-radius: 3px;
}

.card-key .ace    { background: #f2c14e; color: #2a1e05; }
.card-key .birdie { background: #57c26b; color: #06210c; }
.card-key .par    { background: #2f5f36; color: #dff3dd; }
.card-key .bogey  { background: #a3423a; color: #ffe4e0; }

.card-key em {
  margin-left: auto;
  color: #86a882;
  font-style: normal;
  font-size: 8px;
}

/* Only when the card is split across nines - with a single block the last
   column already IS the grand total. */
.card-grand {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding-top: 5px;
  border-top: 1px solid rgba(127, 191, 90, 0.25);
}

.card-grand span {
  display: flex;
  align-items: baseline;
  gap: 5px;
  padding: 2px 7px;
  border-radius: 3px;
  background: rgba(6, 20, 9, 0.7);
}

.card-grand em {
  color: #a8c4a3;
  font-size: 9px;
  font-style: normal;
  font-weight: 800;
  text-transform: uppercase;
}

.card-grand b {
  color: #f6f2e2;
  font-family: ui-monospace, monospace;
  font-size: 14px;
}

.card-grand span.current { box-shadow: inset 0 0 0 1px #f2c14e; }
.card-grand span.current b { color: #f2c14e; }

.card-table + .card-table { margin-top: 4px; }
</style>
