<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import GameArt from '../components/GameArt.vue'

const router = useRouter()
const games = ref([])
const players = ref([])
const selected = ref(null)          // the game whose setup screen is open
const difficulty = ref(null)
const chosenPlayers = ref(new Set())
const starting = ref(false)
const error = ref(null)
const filter = ref('All')

// Per-game extras chosen on the setup screen
const x01Start = ref(501)
const x01Finish = ref('double')   // 'double' | 'straight'
const spaceRoundLimit = ref('')

// The three headline games get bespoke, mock-matched setup screens; the
// difficulty entries themselves still come from the registry.
const KILLER_LIMITS = { hard: 12, normal: 10, easy: 6 }
const KILLER_SLICES = { hard: 1, normal: 2, easy: 3 }
const SPACE_ALIENS = { easy: 20, normal: 31, hard: 45 }

const categories = computed(() => ['All', ...new Set(games.value.map((g) => g.category))])
const shown = computed(() =>
  filter.value === 'All' ? games.value : games.value.filter((g) => g.category === filter.value),
)

const slug = computed(() => selected.value?.slug)
const isKiller = computed(() => slug.value === 'killer')
const isSpace = computed(() => slug.value === 'space-invaders')
const isX01 = computed(() => slug.value === 'x01')

const playerLimit = computed(() => {
  if (!selected.value) return 8
  if (isKiller.value) return KILLER_LIMITS[difficulty.value] ?? selected.value.max_players
  return selected.value.max_players
})

const overLimit = computed(() => chosenPlayers.value.size > playerLimit.value)
const enoughPlayers = computed(
  () => selected.value && chosenPlayers.value.size >= selected.value.min_players,
)
const canStart = computed(() => enoughPlayers.value && !overLimit.value)

const setupHint = computed(() => {
  if (!selected.value) return ''
  if (overLimit.value) {
    if (isKiller.value) {
      return `${difficultyLabel(difficulty.value)} Killer supports up to ${playerLimit.value} players because target slices cannot overlap.`
    }
    return `${selected.value.name} supports up to ${playerLimit.value} players.`
  }
  if (isKiller.value) {
    const slices = KILLER_SLICES[difficulty.value] ?? 1
    return `${slices} highlighted ${slices === 1 ? 'slice' : 'adjacent slices'} per player · targets are assigned automatically.`
  }
  if (isSpace.value) {
    const duration = spaceRoundLimit.value ? `for up to ${spaceRoundLimit.value} rounds` : 'until the fleet is cleared'
    return `${difficultyLabel(difficulty.value)} fleet · ${SPACE_ALIENS[difficulty.value]} aliens · ${duration} · shared defence lives.`
  }
  if (isX01.value) {
    return `Every player starts on ${x01Start.value} · ${x01Finish.value === 'double' ? 'finish on a double' : 'any dart can finish'}.`
  }
  return 'Names and avatars come from your player roster.'
})

function difficultyLabel(key) {
  return { easy: 'Easy', normal: 'Medium', hard: 'Hard' }[key] ?? key
}

async function load() {
  const [cat, roster] = await Promise.all([api.getGameCatalogue(), api.getPlayers()])
  games.value = cat.games
  players.value = roster.players ?? roster
}

function openGame(game) {
  if (!game.available) return
  selected.value = game
  difficulty.value = game.slug === 'killer' ? 'hard' : game.difficulties[1]?.key ?? game.difficulties[0].key
  x01Start.value = 501
  x01Finish.value = 'double'
  spaceRoundLimit.value = ''
  // Default to everyone: the common case is "we're all playing".
  chosenPlayers.value = new Set(players.value.slice(0, game.max_players).map((p) => p.id))
  error.value = null
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function togglePlayer(id) {
  const next = new Set(chosenPlayers.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  chosenPlayers.value = next
}

async function startGame() {
  starting.value = true
  error.value = null
  // Requested first, while the click still counts as a user gesture - browsers
  // refuse requestFullscreen() once an await has broken user activation. This
  // is the real Fullscreen API, which only the machine being clicked on can
  // use; the server also flips presentation mode so a phone-started game
  // fills the screen too.
  try {
    if (!document.fullscreenElement) {
      await document.documentElement.requestFullscreen({ navigationUI: 'hide' })
    }
  } catch { /* a refused fullscreen must never block starting the game */ }
  try {
    let diff = difficulty.value
    let options = null
    if (isX01.value) {
      // The start score is picked directly, so the registry difficulty only
      // supplies defaults the explicit options then override.
      diff = 'normal'
      options = { start: Number(x01Start.value), double_out: x01Finish.value === 'double', double_in: false }
    } else if (isSpace.value) {
      const limit = Number(spaceRoundLimit.value)
      options = Number.isFinite(limit) && limit >= 1 ? { round_limit: Math.min(99, Math.round(limit)) } : {}
    }
    await api.startGame(selected.value.slug, diff, [...chosenPlayers.value], options)
    router.push('/play')
  } catch (err) {
    error.value = err.message
  } finally {
    starting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <!-- ================= setup screen ================= -->
    <div v-if="selected" class="setup-panel" :class="{ 'setup-killer': isKiller, 'setup-space': isSpace, 'setup-x01': isX01 }">
      <header class="setup-header">
        <div>
          <p class="setup-eyebrow">PLAYER SETUP</p>
          <h2>New {{ selected.name }} game</h2>
        </div>
        <button class="abtn secondary" @click="selected = null">Back</button>
      </header>

      <!-- killer difficulty -->
      <div v-if="isKiller" class="option-row">
        <div class="option-copy">
          <small>KILLER DIFFICULTY</small>
          <strong>How many adjacent slices count as each player's target?</strong>
        </div>
        <button
          v-for="key in ['hard', 'normal', 'easy']"
          :key="key"
          type="button"
          class="option-card"
          :class="{ active: difficulty === key }"
          @click="difficulty = key"
        >
          <b>{{ difficultyLabel(key) }}</b>
          <span>{{ KILLER_SLICES[key] }} slice{{ KILLER_SLICES[key] === 1 ? '' : 's' }} · up to {{ KILLER_LIMITS[key] }} players</span>
        </button>
      </div>

      <!-- space invaders configuration -->
      <div v-else-if="isSpace" class="option-row space-row">
        <div class="option-copy">
          <small>INVASION CONFIGURATION</small>
          <strong>Choose fleet strength and mission duration.</strong>
        </div>
        <button
          v-for="key in ['easy', 'normal', 'hard']"
          :key="key"
          type="button"
          class="option-card"
          :class="{ active: difficulty === key }"
          @click="difficulty = key"
        >
          <b>{{ difficultyLabel(key) }}</b>
          <span>{{ SPACE_ALIENS[key] }} aliens</span>
        </button>
        <label class="option-setting">
          <span><small>ROUND LIMIT</small><b>Leave blank to play until the fleet is cleared</b></span>
          <input
            v-model="spaceRoundLimit"
            type="number"
            min="1"
            max="99"
            step="1"
            inputmode="numeric"
            placeholder="Until complete"
          />
        </label>
      </div>

      <!-- x01 start score -->
      <div v-else-if="isX01" class="option-row x01-row">
        <div class="option-copy">
          <small>X01 CONFIGURATION</small>
          <strong>Choose the score each player starts from.</strong>
        </div>
        <label class="option-setting x01-setting">
          <span><small>START SCORE</small><b>Choose 201 through 701</b></span>
          <select v-model="x01Start" aria-label="X01 start score">
            <option v-for="score in [201, 301, 401, 501, 601, 701]" :key="score" :value="score">{{ score }}</option>
          </select>
        </label>
        <label class="option-setting x01-setting">
          <span><small>FINISH</small><b>How the leg must be closed out</b></span>
          <select v-model="x01Finish" aria-label="X01 finish rule">
            <option value="double">Double out</option>
            <option value="straight">Straight out</option>
          </select>
        </label>
      </div>

      <!-- generic difficulties for the rest of the library -->
      <div v-else class="option-row generic-row">
        <div class="option-copy">
          <small>DIFFICULTY</small>
          <strong>Pick how hard the game plays.</strong>
        </div>
        <button
          v-for="d in selected.difficulties"
          :key="d.key"
          type="button"
          class="option-card"
          :class="{ active: difficulty === d.key }"
          @click="difficulty = d.key"
        >
          <b>{{ d.label }}</b>
          <span>{{ d.blurb }}</span>
        </button>
      </div>

      <div class="setup-body">
        <div class="setup-players">
          <h3>Who's playing?</h3>
          <p v-if="players.length === 0" class="muted">
            No players yet — add some on the <router-link to="/players">Players</router-link> page.
          </p>
          <div class="player-picks">
            <button
              v-for="p in players"
              :key="p.id"
              class="player-pick"
              :class="{ active: chosenPlayers.has(p.id) }"
              @click="togglePlayer(p.id)"
            >
              <img :src="p.avatar" alt="" />
              <span>{{ p.name }}</span>
            </button>
          </div>
          <p v-if="!enoughPlayers" class="status error">
            {{ selected.name }} needs at least {{ selected.min_players }} player{{ selected.min_players > 1 ? 's' : '' }}.
          </p>
          <p v-if="error" class="status error">{{ error }}</p>
        </div>

        <aside class="setup-rules">
          <h3>How to play</h3>
          <ol class="rules">
            <li v-for="(rule, i) in selected.rules" :key="i">{{ rule }}</li>
          </ol>
        </aside>
      </div>

      <footer class="setup-footer">
        <span class="setup-hint" :class="{ warn: overLimit }">{{ setupHint }}</span>
        <button class="abtn start" :disabled="!canStart || starting" @click="startGame">
          {{ starting ? 'Starting…' : 'Start game' }}
        </button>
      </footer>
    </div>

    <!-- ================= library ================= -->
    <div v-else>
      <h1>Games</h1>
      <p class="muted">
        Every game runs on the same core — the cameras score your darts, the players are
        the ones on your roster, and the board lights react as you throw. Pick a game to
        set it up.
      </p>

      <div class="filters">
        <button
          v-for="c in categories"
          :key="c"
          class="ghost chip"
          :class="{ active: filter === c }"
          @click="filter = c"
        >{{ c }}</button>
      </div>

      <div class="game-grid">
        <button
          v-for="game in shown"
          :key="game.slug"
          class="game-card"
          :class="{ unavailable: !game.available }"
          :disabled="!game.available"
          @click="openGame(game)"
        >
          <div class="game-art-wrap">
            <GameArt :art="game.art" animate />
            <span v-if="!game.available" class="soon">Coming soon</span>
          </div>
          <div class="game-meta">
            <h3>{{ game.name }}</h3>
            <p class="muted">{{ game.tagline }}</p>
            <span class="players-badge">
              {{ game.min_players === game.max_players
                ? `${game.min_players} players`
                : `${game.min_players}–${game.max_players} players` }}
            </span>
          </div>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}

.chip.active {
  border-color: var(--accent);
  color: var(--accent);
}

.game-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 1rem;
}

.game-card {
  padding: 0;
  overflow: hidden;
  text-align: left;
  background: var(--card, #161a24);
  border: 1px solid var(--border);
  border-radius: 12px;
  cursor: pointer;
  transition: transform 0.15s, border-color 0.15s;
  color: inherit;
  font: inherit;
}

.game-card:hover:not(:disabled) {
  transform: translateY(-3px);
  border-color: var(--accent);
}

.game-card.unavailable {
  opacity: 0.55;
  cursor: default;
}

.game-art-wrap {
  position: relative;
  aspect-ratio: 16 / 9;
  overflow: hidden;
}

.soon {
  position: absolute;
  inset: auto 0.5rem 0.5rem auto;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  font-size: 0.7rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
}

.game-meta {
  padding: 0.7rem 0.85rem 0.9rem;
}

.game-meta h3 {
  margin: 0 0 0.2rem;
}

.game-meta p {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
}

.players-badge {
  font-size: 0.75rem;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
}

/* ================= setup screen ================= */
.setup-panel {
  border: 1px solid #293445;
  border-radius: 15px;
  overflow: hidden;
  background: #101620;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.25);
}

.setup-eyebrow {
  margin: 0 0 4px;
  color: #38d9f1;
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 0.17em;
}

.setup-killer .setup-eyebrow { color: #e940d4; }
.setup-space .setup-eyebrow { color: #45e4ff; }
.setup-x01 .setup-eyebrow { color: #ff7184; }

.setup-header {
  padding: 16px 18px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #293445;
}

.setup-header h2 { margin: 0; font-size: 22px; }

.abtn {
  border: 1px solid rgba(139, 169, 212, 0.22);
  border-radius: 7px;
  padding: 10px 14px;
  background: linear-gradient(155deg, #3479ed 0%, #6948dc 62%, #4b2da5 100%);
  color: white;
  font: inherit;
  font-weight: 800;
  font-size: 12px;
  cursor: pointer;
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.2), 0 6px 16px rgba(0, 0, 0, 0.22);
}

.abtn:hover:not(:disabled) { filter: brightness(1.13); }
.abtn:disabled { opacity: 0.38; cursor: default; }
.abtn.secondary { border-color: rgba(119, 145, 181, 0.26); background: linear-gradient(160deg, rgba(28, 39, 55, 0.98), rgba(13, 19, 29, 0.98)); color: #dce3eb; }
.abtn.start { padding: 13px 26px; font-size: 13px; }

.option-row {
  padding: 13px 18px;
  display: grid;
  grid-template-columns: minmax(220px, 1fr) repeat(3, minmax(140px, 0.5fr));
  gap: 9px;
  align-items: stretch;
  border-bottom: 1px solid #293445;
  background: linear-gradient(90deg, rgba(64, 20, 91, 0.38), rgba(8, 12, 20, 0.96));
}

.setup-space .option-row {
  grid-template-columns: minmax(190px, 0.9fr) repeat(3, minmax(110px, 0.45fr)) minmax(230px, 0.9fr);
  background: linear-gradient(90deg, rgba(7, 32, 55, 0.96), rgba(4, 10, 20, 0.98));
  border-bottom-color: rgba(69, 228, 255, 0.18);
}

.setup-x01 .option-row {
  grid-template-columns: minmax(220px, 1fr) minmax(230px, 0.8fr) minmax(230px, 0.8fr);
  background: linear-gradient(90deg, rgba(49, 16, 30, 0.96), rgba(9, 16, 29, 0.98));
  border-bottom-color: rgba(255, 87, 105, 0.28);
}

.generic-row { grid-template-columns: minmax(220px, 1fr) repeat(3, minmax(140px, 0.5fr)); background: linear-gradient(90deg, rgba(20, 39, 33, 0.8), rgba(8, 12, 20, 0.96)); }

.option-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
}

.option-copy small { color: #e940d4; font-size: 9px; font-weight: 950; letter-spacing: 0.14em; }
.setup-space .option-copy small { color: #45e4ff; }
.setup-x01 .option-copy small { color: #ff7184; }
.generic-row .option-copy small { color: #38b26e; }
.option-copy strong { color: #efe8f6; font-size: 12px; line-height: 1.35; }

.option-card {
  min-width: 0;
  padding: 10px 11px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border: 1px solid rgba(190, 133, 255, 0.25);
  border-radius: 8px;
  background: linear-gradient(145deg, rgba(40, 24, 60, 0.92), rgba(12, 13, 22, 0.96));
  color: #c9c1d4;
  font: inherit;
  text-align: left;
  cursor: pointer;
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.05);
  transition: transform 0.15s, border-color 0.15s;
}

.setup-space .option-card { border-color: rgba(69, 228, 255, 0.2); background: linear-gradient(145deg, rgba(14, 35, 58, 0.96), rgba(6, 13, 25, 0.98)); color: #a9bed0; }

.option-card b { color: white; font-size: 13px; text-transform: uppercase; }
.option-card span { color: #9f95ac; font-size: 10px; }
.option-card:hover { transform: translateY(-1px); border-color: #b650ff; }
.setup-space .option-card:hover { border-color: #45e4ff; }

.option-card.active {
  border-color: #baff19;
  background: linear-gradient(145deg, rgba(139, 218, 29, 0.24), rgba(45, 20, 62, 0.96));
  box-shadow: 0 0 18px rgba(186, 255, 25, 0.13), inset 0 0 0 1px rgba(186, 255, 25, 0.13);
}

.setup-space .option-card.active { background: linear-gradient(145deg, rgba(65, 115, 31, 0.46), rgba(7, 24, 39, 0.98)); }
.option-card.active b { color: #baff19; text-shadow: 0 0 10px rgba(186, 255, 25, 0.45); }

.option-setting {
  padding: 7px 9px 7px 12px;
  display: grid;
  grid-template-columns: 1fr 102px;
  gap: 9px;
  align-items: center;
  border-left: 2px solid rgba(69, 228, 255, 0.48);
  background: rgba(8, 22, 39, 0.76);
}

.setup-x01 .option-setting { border-left-color: rgba(255, 87, 105, 0.64); background: rgba(48, 17, 29, 0.8); }

.option-setting > span { display: flex; flex-direction: column; justify-content: center; gap: 4px; }
.option-setting small { color: #45e4ff; font-size: 9px; font-weight: 950; letter-spacing: 0.15em; }
.setup-x01 .option-setting small { color: #ff7184; }
.option-setting b { color: #dcecf7; font-size: 10px; line-height: 1.35; }

.option-setting input,
.option-setting select {
  width: 100%;
  min-height: 38px;
  border: 1px solid rgba(69, 228, 255, 0.32);
  border-radius: 6px;
  background: #06101e;
  color: white;
  font: inherit;
  font-size: 14px;
  font-weight: 800;
  text-align: center;
}

.setup-x01 .option-setting select { border-color: rgba(255, 113, 132, 0.5); background: #180d18; font-size: 16px; }
.option-setting input::placeholder { color: #7890a5; font-size: 10px; }

.setup-body {
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  gap: 0;
}

.setup-players { padding: 19px; border-right: 1px solid #293445; }
.setup-players h3, .setup-rules h3 { margin: 0 0 12px; }
.setup-rules { padding: 19px; background: #0d121a; }

.rules {
  margin: 0;
  padding-left: 1.2rem;
  line-height: 1.6;
  color: #c5cfda;
  font-size: 0.85rem;
}

.rules li { margin-bottom: 0.35rem; }

.player-picks {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
}

.player-pick {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.8rem 0.35rem 0.4rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font: inherit;
}

.player-pick img {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  object-fit: cover;
  border: 2px solid transparent;
}

.player-pick.active {
  border-color: var(--accent);
  color: var(--text);
}

.player-pick.active img { border-color: var(--accent); }

.setup-footer {
  padding: 14px 18px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  border-top: 1px solid #293445;
}

.setup-hint { color: var(--muted); font-size: 0.8rem; }
.setup-hint.warn { color: #ffbf4d; }

@media (max-width: 1000px) {
  .option-row, .setup-space .option-row, .setup-x01 .option-row, .generic-row { grid-template-columns: 1fr 1fr; }
  .option-copy { grid-column: 1 / -1; }
  .setup-body { grid-template-columns: 1fr; }
  .setup-players { border-right: 0; border-bottom: 1px solid #293445; }
}
</style>
