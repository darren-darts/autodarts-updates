<script setup>
// Phone game library: pick a game, set its options, choose who's playing,
// start it on the main screen. Same options as the desktop setup screens, so
// a game started from a phone is identical to one started at the TV.
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import GameArt from './GameArt.vue'

const emit = defineEmits(['started'])

const games = ref([])
const players = ref([])
const selected = ref(null)
const difficulty = ref(null)
const chosen = ref(new Set())
const starting = ref(false)
const error = ref(null)

const x01Start = ref(501)
const x01Finish = ref('double')
const spaceRoundLimit = ref('')

const KILLER_LIMITS = { hard: 12, normal: 10, easy: 6 }
const KILLER_SLICES = { hard: 1, normal: 2, easy: 3 }
const SPACE_ALIENS = { easy: 20, normal: 31, hard: 45 }

const slug = computed(() => selected.value?.slug)
const isKiller = computed(() => slug.value === 'killer')
const isSpace = computed(() => slug.value === 'space-invaders')
const isX01 = computed(() => slug.value === 'x01')

const playerLimit = computed(() => {
  if (!selected.value) return 8
  if (isKiller.value) return KILLER_LIMITS[difficulty.value] ?? selected.value.max_players
  return selected.value.max_players
})
const overLimit = computed(() => chosen.value.size > playerLimit.value)
const enough = computed(() => selected.value && chosen.value.size >= selected.value.min_players)
const canStart = computed(() => enough.value && !overLimit.value)

const label = (key) => ({ easy: 'Easy', normal: 'Medium', hard: 'Hard' }[key] ?? key)

const hint = computed(() => {
  if (!selected.value) return ''
  if (overLimit.value) return `Up to ${playerLimit.value} players for this setting.`
  if (!enough.value) return `Needs at least ${selected.value.min_players} player${selected.value.min_players > 1 ? 's' : ''}.`
  if (isKiller.value) {
    const s = KILLER_SLICES[difficulty.value] ?? 1
    return `${s} highlighted ${s === 1 ? 'slice' : 'adjacent slices'} each · targets assigned automatically.`
  }
  if (isSpace.value) {
    return `${SPACE_ALIENS[difficulty.value]} aliens · ${spaceRoundLimit.value ? `${spaceRoundLimit.value} rounds` : 'until the fleet is cleared'}.`
  }
  if (isX01.value) {
    return `Start on ${x01Start.value} · ${x01Finish.value === 'double' ? 'finish on a double' : 'any dart can finish'}.`
  }
  return 'Ready to start.'
})

async function load() {
  try {
    const [cat, roster] = await Promise.all([api.getGameCatalogue(), api.getPlayers()])
    games.value = cat.games
    players.value = roster.players ?? roster
  } catch (err) {
    error.value = err.message
  }
}

function open(game) {
  if (!game.available) return
  selected.value = game
  difficulty.value = game.slug === 'killer' ? 'hard' : game.difficulties[1]?.key ?? game.difficulties[0].key
  x01Start.value = 501
  x01Finish.value = 'double'
  spaceRoundLimit.value = ''
  chosen.value = new Set(players.value.slice(0, game.max_players).map((p) => p.id))
  error.value = null
}

function toggle(id) {
  const next = new Set(chosen.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  chosen.value = next
}

async function start() {
  starting.value = true
  error.value = null
  try {
    let diff = difficulty.value
    let options = null
    if (isX01.value) {
      diff = 'normal'
      options = { start: Number(x01Start.value), double_out: x01Finish.value === 'double', double_in: false }
    } else if (isSpace.value) {
      const limit = Number(spaceRoundLimit.value)
      options = Number.isFinite(limit) && limit >= 1 ? { round_limit: Math.min(99, Math.round(limit)) } : {}
    }
    await api.startGame(selected.value.slug, diff, [...chosen.value], options)
    selected.value = null
    emit('started')
  } catch (err) {
    error.value = err.message
  } finally {
    starting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="library">
    <!-- setup for the chosen game -->
    <div v-if="selected">
      <div class="setup-head">
        <h2>{{ selected.name }}</h2>
        <button class="ghost" @click="selected = null">Back</button>
      </div>

      <div v-if="isKiller || isSpace || (!isX01 && selected.difficulties.length)" class="card option-card">
        <h3>{{ isKiller ? 'Target slices' : isSpace ? 'Fleet strength' : 'Difficulty' }}</h3>
        <div class="option-row">
          <button
            v-for="d in (isKiller ? ['hard', 'normal', 'easy'] : isSpace ? ['easy', 'normal', 'hard'] : selected.difficulties.map((x) => x.key))"
            :key="d"
            class="option"
            :class="{ active: difficulty === d }"
            @click="difficulty = d"
          >
            <b>{{ label(d) }}</b>
            <span v-if="isKiller">{{ KILLER_SLICES[d] }} slice{{ KILLER_SLICES[d] === 1 ? '' : 's' }} · max {{ KILLER_LIMITS[d] }}</span>
            <span v-else-if="isSpace">{{ SPACE_ALIENS[d] }} aliens</span>
            <span v-else>{{ selected.difficulties.find((x) => x.key === d)?.blurb }}</span>
          </button>
        </div>
        <label v-if="isSpace" class="field">
          <span>Round limit — blank plays until the fleet is cleared</span>
          <input v-model="spaceRoundLimit" type="number" min="1" max="99" inputmode="numeric" placeholder="Until complete" />
        </label>
      </div>

      <div v-if="isX01" class="card option-card">
        <h3>X01 setup</h3>
        <label class="field">
          <span>Start score</span>
          <select v-model="x01Start">
            <option v-for="s in [201, 301, 401, 501, 601, 701]" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="field">
          <span>Finish</span>
          <select v-model="x01Finish">
            <option value="double">Double out</option>
            <option value="straight">Straight out</option>
          </select>
        </label>
      </div>

      <div class="card option-card">
        <h3>Who's playing?</h3>
        <p v-if="!players.length" class="muted">No players yet — add some on the Players tab.</p>
        <div class="picks">
          <button
            v-for="p in players"
            :key="p.id"
            class="pick"
            :class="{ active: chosen.has(p.id) }"
            @click="toggle(p.id)"
          >
            <img :src="p.avatar" alt="" />
            <span>{{ p.name }}</span>
          </button>
        </div>
      </div>

      <details class="card rules-card">
        <summary>How to play</summary>
        <ol>
          <li v-for="(rule, i) in selected.rules" :key="i">{{ rule }}</li>
        </ol>
      </details>

      <p v-if="error" class="status error">{{ error }}</p>
      <p class="muted hint">{{ hint }}</p>
      <button class="primary start" :disabled="!canStart || starting" @click="start">
        {{ starting ? 'Starting…' : 'Start on the main screen' }}
      </button>
    </div>

    <!-- game grid -->
    <div v-else>
      <p class="muted intro">Pick a game — it starts on the main screen.</p>
      <p v-if="error" class="status error">{{ error }}</p>
      <div class="grid">
        <button
          v-for="game in games"
          :key="game.slug"
          class="tile"
          :class="{ unavailable: !game.available }"
          :disabled="!game.available"
          @click="open(game)"
        >
          <div class="art"><GameArt :art="game.art" /></div>
          <div class="tile-meta">
            <strong>{{ game.name }}</strong>
            <span>{{ game.tagline }}</span>
            <em v-if="!game.available">Coming soon</em>
          </div>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.library {
  text-align: left;
}

.intro {
  margin: 0 0 0.7rem;
  font-size: 0.85rem;
}

.grid {
  display: grid;
  gap: 0.6rem;
}

.tile {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 0.7rem;
  padding: 0;
  overflow: hidden;
  align-items: stretch;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--panel);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.tile.unavailable {
  opacity: 0.5;
}

.art {
  aspect-ratio: 16 / 10;
  overflow: hidden;
}

.tile-meta {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 0.15rem;
  padding: 0.55rem 0.6rem 0.55rem 0;
}

.tile-meta strong {
  font-size: 1rem;
}

.tile-meta span {
  color: var(--muted);
  font-size: 0.75rem;
  line-height: 1.35;
}

.tile-meta em {
  color: var(--muted);
  font-size: 0.66rem;
  font-style: normal;
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.setup-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
  margin-bottom: 0.7rem;
}

.setup-head h2 {
  margin: 0;
}

.option-card {
  margin-bottom: 0.6rem;
}

.option-card h3 {
  margin: 0 0 0.6rem;
  font-size: 0.95rem;
}

.option-row {
  display: grid;
  gap: 0.4rem;
}

.option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.1rem;
  padding: 0.6rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.option.active {
  border-color: var(--accent);
  background: rgba(56, 178, 110, 0.12);
}

.option b {
  font-size: 0.92rem;
}

.option span {
  color: var(--muted);
  font-size: 0.74rem;
}

.field {
  display: block;
  margin-top: 0.6rem;
}

.field span {
  display: block;
  margin-bottom: 0.3rem;
  color: var(--muted);
  font-size: 0.78rem;
}

.field input,
.field select {
  width: 100%;
  min-height: 44px;
  padding: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
  color: var(--text);
  font: inherit;
}

.picks {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}

.pick {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.7rem 0.3rem 0.3rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 0.85rem;
  cursor: pointer;
}

.pick img {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  object-fit: cover;
}

.pick.active {
  border-color: var(--accent);
  color: var(--text);
}

.rules-card summary {
  cursor: pointer;
  font-weight: 600;
}

.rules-card ol {
  margin: 0.6rem 0 0;
  padding-left: 1.1rem;
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1.55;
}

.hint {
  margin: 0.5rem 0;
  font-size: 0.8rem;
}

.start {
  width: 100%;
  padding: 0.95rem;
  font-size: 1rem;
}
</style>
