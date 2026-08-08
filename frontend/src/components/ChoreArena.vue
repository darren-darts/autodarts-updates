<script setup>
// The Mr vs Mrs screen: two scoreboards, the chore being thrown for, the live
// board, and the running list of who ended up with what.
//
// Chore artwork is optional by design. Each chore looks for /chores/<id>.png
// and falls back to its emoji if that file is not there, so the game is fully
// playable before any artwork is sliced (see tools/slice_chore_images.py) and
// upgrades itself the moment the images are dropped in.
import { computed, ref } from 'vue'
import DartboardFace from './DartboardFace.vue'
import WheelOfMisfortune from './WheelOfMisfortune.vue'

const props = defineProps({
  players: { type: Array, default: () => [] },
  game: { type: Object, default: () => ({}) },
  darts: { type: Array, default: () => [] },
  geometry: { type: Object, default: null },
  highlight: { type: Array, default: () => [] },
  finished: { type: Boolean, default: false },
  winnerId: { type: String, default: null },
  message: { type: String, default: '' },
})

// Index 0 is MR, index 1 is MRS - the roster order picked on the setup screen.
const SIDES = ['MR', 'MRS']

const missingArt = ref(new Set())
const artFor = (id) => (id && !missingArt.value.has(id) ? `/chores/${id}.png` : null)
function artFailed(id) {
  // A new Set, not a mutation: Vue tracks the ref, not the Set's contents.
  missingArt.value = new Set(missingArt.value).add(id)
}

const chore = computed(() => props.game.chore ?? {})
const rounds = computed(() => props.game.rounds ?? 10)
const roundNo = computed(() => props.game.chore_round ?? 1)
const thrown = computed(() => props.game.thrown ?? [])
const results = computed(() => props.game.results ?? [])
const library = computed(() => props.game.library ?? [])

const choreById = computed(() => Object.fromEntries(library.value.map((c) => [c.id, c])))
const scoreFor = (playerId) => thrown.value.find((t) => t.player_id === playerId)?.score
const isThrowing = (playerId) => !props.finished && props.game.throwing_player_id === playerId

// Every round gets a row, played or not, so the card reads as a week's plan
// rather than a growing list.
const historyRows = computed(() => {
  const played = new Map(results.value.map((r) => [r.round, r]))
  return Array.from({ length: rounds.value }, (_, i) => {
    const result = played.get(i + 1)
    if (!result) return { round: i + 1 }
    return {
      round: i + 1,
      ...result,
      chore: choreById.value[result.chore] ?? { label: result.chore, emoji: '' },
      loser: props.players.find((p) => p.player_id === result.loser_id),
    }
  })
})

const roundsWon = (player) => player.score ?? 0
const listFor = (playerId) => props.game.assignments?.[playerId] ?? []
const bonusesFor = (playerId) => props.game.bonuses?.[playerId] ?? []
const rewardsFor = (playerId) => props.game.rewards?.[playerId] ?? []
const choreTotal = (playerId) => listFor(playerId).reduce((sum, entry) => sum + entry.count, 0)

const spinnerName = computed(() => {
  if (props.game.wheel) return props.game.wheel.player
  const trailing = [...props.players].sort((a, b) => roundsWon(a) - roundsWon(b))[0]
  return trailing?.name ?? ''
})

const champion = computed(() => props.players.find((p) => p.player_id === props.winnerId))
</script>

<template>
  <section class="chore-arena">
    <!-- ------------------------------------------------- round history -->
    <aside class="chore-rail">
      <div class="rail-head"><span>ROUND HISTORY</span></div>
      <ol class="rail-rounds">
        <li v-for="row in historyRows" :key="row.round" :class="{ played: row.chore, now: row.round === roundNo && !finished }">
          <b>R{{ row.round }}</b>
          <template v-if="row.chore">
            <span class="rail-chore"><i>{{ row.chore.emoji }}</i>{{ row.chore.label }}</span>
            <em :class="{ mr: row.winner_id === players[0]?.player_id }">{{ row.loser?.name }}</em>
          </template>
          <span v-else class="rail-empty">—</span>
        </li>
      </ol>
      <p class="rail-foot">👑 WIN THE MOST ROUNDS TO BE CROWNED HOUSEHOLD CHAMPION</p>
    </aside>

    <!-- ------------------------------------------------- the round itself -->
    <div class="chore-main">
      <header class="chore-scoreboard">
        <article
          v-for="(player, index) in players"
          :key="player.player_id"
          class="chore-player"
          :class="[index === 0 ? 'mr' : 'mrs', {
            throwing: isThrowing(player.player_id),
            champion: finished && player.player_id === winnerId,
          }]"
        >
          <img :src="player.avatar" alt="" />
          <div class="chore-who">
            <small>{{ SIDES[index] }}</small>
            <strong>{{ player.name }}</strong>
          </div>
          <div class="chore-tally">
            <b>{{ roundsWon(player) }}</b>
            <span>ROUNDS</span>
          </div>
          <span v-if="scoreFor(player.player_id) != null" class="chore-throw">THREW {{ scoreFor(player.player_id) }}</span>
          <span v-else-if="isThrowing(player.player_id)" class="chore-throw live">TO THROW</span>
        </article>
      </header>

      <div class="chore-centre">
        <div class="chore-card" :class="{ double: game.double }">
          <span class="chore-round">ROUND {{ roundNo }} / {{ rounds }}</span>
          <div class="chore-image">
            <img
              v-if="artFor(chore.id)"
              :src="artFor(chore.id)"
              alt=""
              @error="artFailed(chore.id)"
            />
            <i v-else>{{ chore.emoji }}</i>
          </div>
          <strong>{{ chore.label }}</strong>
          <span v-if="game.double" class="chore-flame">🔥 DOUBLE TROUBLE — LOSER DOES IT TWICE</span>
          <span v-else class="chore-sub">Highest dart avoids it</span>
        </div>

        <div class="chore-board">
          <DartboardFace
            v-if="geometry"
            :geometry="geometry"
            fluid
            theme="classic"
            :highlight="highlight"
            :darts="darts"
          />
        </div>
      </div>

      <p class="chore-message">{{ message || 'Both players throw one dart.' }}</p>
    </div>

    <!-- ------------------------------------------------- specials + wheel -->
    <aside class="chore-side">
      <div v-if="game.lucky" class="special lucky">
        <span>LUCKY TARGET</span>
        <b>{{ game.lucky.segment }}</b>
        <small>Hit it for a bonus</small>
      </div>
      <div v-if="game.steal" class="special steal">
        <span>🥷 STEAL ROUND</span>
        <small>The winner moves a chore around</small>
      </div>

      <WheelOfMisfortune
        :segments="game.wheel_segments || []"
        :result="game.wheel"
        :spinner-name="spinnerName"
      />

      <!-- The lists are the point of the whole game: they stay up at the end
           so the week's chores can be photographed off the screen. -->
      <div class="chore-lists" :class="{ final: finished }">
        <article v-for="(player, index) in players" :key="player.player_id" :class="index === 0 ? 'mr' : 'mrs'">
          <header>
            <strong>{{ player.name }}</strong>
            <span>{{ choreTotal(player.player_id) }} {{ choreTotal(player.player_id) === 1 ? 'JOB' : 'JOBS' }}</span>
          </header>
          <ul v-if="listFor(player.player_id).length">
            <li v-for="(entry, i) in listFor(player.player_id)" :key="i" :class="{ doubled: entry.count > 1 }">
              <i>{{ entry.emoji }}</i>
              <span>{{ entry.label }}</span>
              <b v-if="entry.count > 1">×{{ entry.count }}</b>
            </li>
          </ul>
          <p v-else class="chore-none">Nothing yet 😇</p>
          <div v-if="bonusesFor(player.player_id).length" class="chore-bonuses">
            <span v-for="(bonus, i) in bonusesFor(player.player_id)" :key="i" :title="bonus.detail">
              {{ bonus.emoji }} {{ bonus.label }}
            </span>
          </div>
          <div v-if="rewardsFor(player.player_id).length" class="chore-rewards">
            <span v-for="(reward, i) in rewardsFor(player.player_id)" :key="i">🎉 {{ reward }}</span>
          </div>
        </article>
      </div>

      <p v-if="finished" class="chore-champion">
        <template v-if="champion">👑 {{ champion.name }} IS HOUSEHOLD CHAMPION</template>
        <template v-else>🤝 HONOURS EVEN — IT'S A DRAW</template>
      </p>
    </aside>
  </section>
</template>

<style scoped>
.chore-arena { min-width: 0; min-height: 0; display: grid; grid-template-columns: 232px minmax(0, 1fr) 268px; gap: 12px; }

/* ------------------------------------------------------------ history rail */
.chore-rail { min-height: 0; padding: 11px 10px; display: flex; flex-direction: column; gap: 8px; border: 1px solid rgba(120, 160, 220, 0.28); border-radius: 14px; background: linear-gradient(180deg, rgba(14, 22, 42, 0.95), rgba(9, 14, 28, 0.95)); }
.rail-head span { color: #cfe4ff; font-size: 10px; font-weight: 900; letter-spacing: 0.16em; }
.rail-rounds { margin: 0; padding: 0; min-height: 0; flex: 1 1 auto; overflow: auto; list-style: none; display: flex; flex-direction: column; gap: 3px; }
.rail-rounds li { min-height: 26px; padding: 3px 6px; display: grid; grid-template-columns: 24px minmax(0, 1fr) auto; align-items: center; gap: 6px; border-radius: 6px; background: rgba(255, 255, 255, 0.03); font-size: 10px; }
.rail-rounds li.now { background: rgba(56, 217, 241, 0.14); box-shadow: inset 0 0 0 1px rgba(56, 217, 241, 0.45); }
.rail-rounds b { color: #7f95bb; font-size: 9px; font-weight: 900; }
.rail-chore { min-width: 0; display: flex; align-items: center; gap: 5px; overflow: hidden; color: #e7eefc; text-overflow: ellipsis; white-space: nowrap; }
.rail-chore i { font-style: normal; }
.rail-rounds em { color: #ff3d8b; font-size: 9px; font-style: normal; font-weight: 900; letter-spacing: 0.04em; text-transform: uppercase; }
.rail-rounds em.mr { color: #ff3d8b; }
.rail-empty { color: #47597a; }
.rail-foot { margin: 0; color: #ffd12e; font-size: 8px; font-weight: 900; letter-spacing: 0.08em; line-height: 1.5; text-align: center; }

/* ------------------------------------------------------------ scoreboard */
.chore-main { min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 10px; }
.chore-scoreboard { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.chore-player { position: relative; padding: 9px 12px; display: grid; grid-template-columns: 46px minmax(0, 1fr) auto; align-items: center; gap: 10px; border: 2px solid transparent; border-radius: 14px; background: linear-gradient(120deg, rgba(20, 32, 58, 0.95), rgba(12, 20, 38, 0.95)); }
.chore-player img { width: 46px; height: 46px; border: 2px solid currentColor; border-radius: 50%; object-fit: cover; background: #111722; }
.chore-player.mr { color: #38bdf8; }
.chore-player.mrs { color: #ff3d8b; }
.chore-player.throwing { border-color: currentColor; box-shadow: 0 0 22px -4px currentColor; }
.chore-player.champion { border-color: #ffd12e; box-shadow: 0 0 26px -6px #ffd12e; }
.chore-who { min-width: 0; display: flex; flex-direction: column; }
.chore-who small { color: currentColor; font-size: 10px; font-weight: 900; letter-spacing: 0.18em; }
.chore-who strong { overflow: hidden; color: #f2f7ff; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
.chore-tally { display: flex; flex-direction: column; align-items: center; }
.chore-tally b { color: #fff; font-size: 30px; line-height: 0.95; }
.chore-tally span { color: #7f95bb; font-size: 7px; font-weight: 900; letter-spacing: 0.14em; }
.chore-throw { position: absolute; right: 10px; bottom: -9px; padding: 2px 8px; border-radius: 99px; background: #1d2c4d; color: #cfe4ff; font-size: 8px; font-weight: 900; letter-spacing: 0.1em; }
.chore-throw.live { background: currentColor; color: #06101f; }

/* ------------------------------------------------------------ chore + board */
.chore-centre { min-height: 0; flex: 1 1 auto; display: grid; grid-template-columns: minmax(0, 260px) minmax(0, 1fr); align-items: center; gap: 14px; }
.chore-card { padding: 12px; display: flex; flex-direction: column; align-items: center; gap: 7px; border: 2px solid rgba(120, 160, 220, 0.35); border-radius: 16px; background: linear-gradient(180deg, rgba(18, 28, 52, 0.96), rgba(10, 16, 32, 0.96)); text-align: center; }
.chore-card.double { border-color: rgba(255, 122, 26, 0.75); box-shadow: 0 0 30px -8px rgba(255, 122, 26, 0.9); }
.chore-round { padding: 3px 10px; border-radius: 99px; background: rgba(255, 255, 255, 0.08); color: #cfe4ff; font-size: 9px; font-weight: 900; letter-spacing: 0.14em; }
.chore-image { width: 100%; aspect-ratio: 1; display: grid; overflow: hidden; place-items: center; border-radius: 12px; background: rgba(255, 255, 255, 0.05); }
.chore-image img { width: 100%; height: 100%; object-fit: cover; }
.chore-image i { font-size: 72px; font-style: normal; line-height: 1; }
.chore-card strong { color: #fff; font-size: 21px; letter-spacing: 0.02em; line-height: 1.1; text-transform: uppercase; }
.chore-sub { color: #7f95bb; font-size: 9px; font-weight: 800; letter-spacing: 0.1em; text-transform: uppercase; }
.chore-flame { color: #ffa24d; font-size: 9px; font-weight: 900; letter-spacing: 0.08em; }
.chore-board { min-width: 0; display: grid; place-items: center; }
.chore-board > * { width: min(100%, 52vh); }
.chore-message { margin: 0; padding: 8px 12px; border-radius: 10px; background: rgba(255, 255, 255, 0.05); color: #dce7f8; font-size: 12px; font-weight: 700; text-align: center; }

/* ------------------------------------------------------------ side rail */
.chore-side { min-height: 0; display: flex; flex-direction: column; gap: 10px; overflow: auto; }
.special { padding: 8px 11px; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 4px; border-radius: 12px; }
.special span { font-size: 10px; font-weight: 900; letter-spacing: 0.1em; }
.special small { grid-column: 1 / -1; font-size: 9px; font-weight: 700; }
.special.lucky { border: 1px solid rgba(255, 209, 46, 0.55); background: rgba(255, 209, 46, 0.12); color: #ffe27a; }
.special.lucky b { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 50%; background: #ffd12e; color: #3a2600; font-size: 16px; }
.special.steal { border: 1px solid rgba(180, 108, 255, 0.55); background: rgba(180, 108, 255, 0.14); color: #d5b3ff; }

.chore-lists { display: flex; flex-direction: column; gap: 8px; }
.chore-lists article { padding: 9px 10px; border: 1px solid rgba(120, 160, 220, 0.28); border-left: 3px solid currentColor; border-radius: 10px; background: rgba(12, 20, 38, 0.9); }
.chore-lists .mr { color: #38bdf8; }
.chore-lists .mrs { color: #ff3d8b; }
.chore-lists.final article { box-shadow: 0 0 0 1px currentColor; }
.chore-lists header { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.chore-lists header strong { color: #f2f7ff; font-size: 13px; }
.chore-lists header span { color: currentColor; font-size: 9px; font-weight: 900; letter-spacing: 0.1em; }
.chore-lists ul { margin: 6px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 3px; }
.chore-lists li { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; align-items: center; gap: 5px; color: #dce7f8; font-size: 11px; }
.chore-lists li i { font-style: normal; }
.chore-lists li span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chore-lists li.doubled b { color: #ffa24d; font-size: 10px; }
.chore-none { margin: 6px 0 0; color: #6f85a8; font-size: 11px; }
.chore-bonuses, .chore-rewards { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.chore-bonuses span { padding: 2px 6px; border-radius: 99px; background: rgba(255, 209, 46, 0.16); color: #ffe27a; font-size: 9px; font-weight: 800; }
.chore-rewards span { padding: 2px 6px; border-radius: 99px; background: rgba(63, 196, 99, 0.16); color: #8ef0a8; font-size: 9px; font-weight: 800; }

.chore-champion { margin: 0; padding: 9px; border-radius: 10px; background: linear-gradient(90deg, rgba(255, 209, 46, 0.2), rgba(255, 122, 26, 0.2)); color: #ffe27a; font-size: 12px; font-weight: 900; letter-spacing: 0.06em; text-align: center; }

@media (max-width: 1400px) {
  .chore-arena { grid-template-columns: 200px minmax(0, 1fr) 240px; }
  .chore-centre { grid-template-columns: minmax(0, 220px) minmax(0, 1fr); }
}

@media (max-width: 1100px) {
  .chore-arena { grid-template-columns: minmax(0, 1fr); }
  .chore-rail { max-height: 220px; }
}
</style>
