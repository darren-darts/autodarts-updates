<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { playHitSound, playReviewSound, playTakeoutSound, unlockAudio } from '../sound'
import DartboardFace from '../components/DartboardFace.vue'
import SpaceStage from '../components/SpaceStage.vue'
import BoardPicker from '../components/BoardPicker.vue'
import TakeoutPrompt from '../components/TakeoutPrompt.vue'
import GolfScorecard from '../components/GolfScorecard.vue'
import DerbyRace from '../components/DerbyRace.vue'
import ChoreArena from '../components/ChoreArena.vue'
import SnakesBoard from '../components/SnakesBoard.vue'

const router = useRouter()
const state = ref({ active: false })
const busy = ref(false)
const detection = ref(null)
const geometry = ref(null)

// Override / miss dialog
const overrideOpen = ref(false)
const overrideAction = ref('add') // 'replace' | 'add'
const overrideTarget = ref('MISS')
const helpOpen = ref(false)

// Fullscreen + remote presentation
const isFullscreen = ref(false)
const presentation = ref(false)
const winnerDismissed = ref(false)
const takingOut = ref(false)   // briefly true while darts are being removed

let ws = null
let poll = null
let takeoutTimer = null

const ACCENTS = ['#ff3a8c', '#ff8a28', '#d14cff', '#28d9ef', '#f0d629', '#75e96e', '#4f84ff', '#ff6a67']
const accent = (index) => ACCENTS[index % ACCENTS.length]

function avatarFor(player) {
  if (player?.avatar) return player.avatar
  const initial = String(player?.name || 'P').trim().charAt(0).toUpperCase() || 'P'
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#4bd9ed"/><stop offset="1" stop-color="#8e3df0"/></linearGradient></defs><rect width="240" height="240" fill="#111722"/><circle cx="120" cy="120" r="93" fill="url(#g)"/><text x="120" y="152" fill="white" font-family="Arial" font-size="103" font-weight="800" text-anchor="middle">${initial}</text></svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

const game = computed(() => state.value.game ?? {})
const kind = computed(() => game.value.kind ?? '')
const players = computed(() => state.value.players ?? [])
const currentIndex = computed(() => players.value.findIndex((p) => p.player_id === state.value.current_player_id))
const current = computed(() => players.value[currentIndex.value])
const winner = computed(() => players.value.find((p) => p.player_id === state.value.winner_id))
const ranked = computed(() => [...players.value].sort((a, b) => (a.place ?? 99) - (b.place ?? 99)))
const darts = computed(() => state.value.darts_this_turn ?? [])
const dartNumber = computed(() => Math.min(state.value.darts_per_turn ?? 3, darts.value.length + 1))

const derbyPlayers = computed(() => players.value.map((player) => ({
  ...player,
  avatar: avatarFor(player),
})))
const derbyNumber = (playerId) => game.value.numbers?.[playerId] ?? '—'

// Mr vs Mrs plays one dart each inside a single engine turn, so the player who
// is actually at the oche is not always the engine's current player - the game
// reports it. See backend/games/chores.py for why a round is one turn.
const chorePlayers = computed(() => players.value.map((player) => ({
  ...player,
  avatar: avatarFor(player),
})))
const choreThrower = computed(
  () => players.value.find((p) => p.player_id === game.value.throwing_player_id) ?? current.value,
)

function derbyDartEffect(dart) {
  const match = /^([SDT])(\d+)$/.exec(dart?.label ?? '')
  if (!match) return 'NO MOVEMENT'
  const steps = { S: 1, D: 2, T: 3 }[match[1]]
  const number = Number(match[2])
  if (number === derbyNumber(current.value?.player_id)) return `+${steps} FORWARD`
  const rival = players.value.find((player) => derbyNumber(player.player_id) === number)
  return rival ? `-${steps} ${rival.name.toUpperCase()}` : 'NO MOVEMENT'
}

// Snakes & Ladders: tokens carry the player's accent so the board and the
// console name the same colour, and the running move is the summed, capped
// total of the darts thrown so far this visit.
const snakePlayers = computed(() => players.value.map((player, index) => ({
  ...player,
  avatar: avatarFor(player),
  accent: accent(index),
})))
const snakeSquare = (playerId) => game.value.positions?.[playerId] ?? 0
// One dart at a time: a dart's score becomes ceil(score / divisor) squares.
// Mirrors backend/games/snakes.py movement_for so the console and the move agree.
const snakeDartMove = (dart) => Math.ceil((dart?.score || 0) / (game.value.divisor || 5))

// Templates call .toUpperCase() on this, so it must always be a string.
// Golf is the only game here where LOW wins, so the leader is the minimum and
// "to par" counts against 3 strokes per hole actually played, not against the
// whole course - a player two holes in should not read as 48 under.
const golfLeader = computed(() => {
  if (kind.value !== 'golf' || !players.value.length) return null
  return players.value.reduce((best, p) => ((p.score ?? 0) < (best.score ?? 0) ? p : best))
})

function golfToPar(player) {
  if (!player) return ''
  const played = (game.value.cards?.[player.player_id] ?? []).length
  if (!played) return 'not started'
  const diff = (player.score ?? 0) - played * (game.value.par ?? 3)
  if (diff === 0) return 'level par'
  return diff > 0 ? `+${diff}` : `${diff}`
}

// Noughts & Crosses. The backend computes the tactical suggestion for BOTH
// players (view() has no idea whose turn it is); the UI picks the current one.
const oxoSymbol = computed(() => game.value.symbols?.[state.value.current_player_id] ?? '')
const oxoSuggested = computed(() => {
  const index = game.value.suggested?.[state.value.current_player_id]
  return typeof index === 'number' ? index : null
})

const nextPlayerName = computed(() => {
  const list = players.value.filter((p) => !p.finished)
  if (list.length < 2) return null
  const here = list.findIndex((p) => p.player_id === state.value.current_player_id)
  return list[(here + 1) % list.length]?.name ?? null
})

const difficultyName = computed(
  () => ({ easy: 'Easy', normal: 'Medium', hard: 'Hard' }[state.value.difficulty] ?? state.value.difficulty ?? ''),
)

const title = computed(() => {
  if (kind.value === 'killer') return `Killer · ${difficultyName.value} hunt`
  if (kind.value === 'invaders') return `Space Invaders · ${difficultyName.value}`
  if (kind.value === 'derby') return `Donkey Derby · ${difficultyName.value} · ${game.value.track ?? 12} furlongs`
  if (kind.value === 'snakes') return `Snakes & Ladders · first to ${game.value.finish ?? 100}`
  if (kind.value === 'x01') return 'Classic darts · X01'
  return state.value.name || 'Now playing'
})

// ---------------------------------------------------------------- detector chip
// Detection is now Autodarts (it owns the cameras, calibration and dart
// localisation); this chip reflects its health from /api/detection/autodarts.
// The four states map to the one thing the player needs to know - will a
// thrown dart score right now, and if not, what to fix.
const scan = computed(() => {
  const d = detection.value
  if (!d) return { label: 'CONNECTING…', detail: 'Contacting the Autodarts detector', tone: 'working' }
  if (!d.available) return { label: 'AUTODARTS OFFLINE', detail: 'Detection service unreachable — start Autodarts, then check the Board Manager', tone: 'bad' }
  if (!d.connected) return { label: 'CAMERAS OFFLINE', detail: 'Autodarts is up but its cameras aren’t connected — open the Board Manager (:3180)', tone: 'bad' }
  if (!d.running) return { label: 'BOARD IDLE', detail: 'Start a game in Autodarts (lobby) so it begins detecting throws', tone: 'warn' }
  if (d.stuck) return { label: 'BOARD STUCK — RESETTING', detail: 'Autodarts is wedged in a takeout; auto-resetting to recover. If it keeps happening, fix the board lighting / camera exposure.', tone: 'warn' }
  return { label: 'SCANNING', detail: 'Autodarts live · darts score automatically', tone: 'good' }
})

// ---------------------------------------------------------------- killer helpers
const killerTargets = (pid) => (game.value.targets?.[pid] ?? []).join(' & ')
const killerMarks = (pid) => game.value.marks?.[pid] ?? 0
const isKiller = (pid) => (game.value.killers ?? []).includes(pid)
const killerObjective = computed(() => {
  const p = current.value
  if (!p) return ''
  if (isKiller(p.player_id)) return 'You are a KILLER — hit any highlighted opponent slice'
  const left = (game.value.marks_to_kill ?? 3) - killerMarks(p.player_id)
  return `Hit ${killerTargets(p.player_id)} — ${left} more mark${left === 1 ? '' : 's'} to become a Killer`
})

// ---------------------------------------------------------------- invaders helpers
const invLives = computed(() => game.value.invasion_lives ?? 3)
const invRemaining = computed(() => game.value.aliens_remaining ?? 0)
const invTotal = computed(() => Math.max(1, game.value.alien_total ?? 1))
const fleetPercent = computed(() => Math.round((invRemaining.value / invTotal.value) * 100))
const invRoundLabel = computed(() =>
  game.value.round_limit == null
    ? `${state.value.round ?? 1} · UNTIL CLEAR`
    : `${state.value.round ?? 1} / ${game.value.round_limit}`,
)
const cannonArmed = computed(() => Boolean(game.value.cannons?.[current.value?.player_id]))
const invLeader = computed(() =>
  [...players.value].sort((a, b) => (b.score || 0) - (a.score || 0))[0] ?? null,
)
const invKills = (pid) => game.value.kills?.[pid] ?? 0

// ---------------------------------------------------------------- actions
async function refresh() {
  try { state.value = await api.getGameState() } catch { /* transient */ }
  try { detection.value = await api.getAutodartsStatus() } catch { /* transient */ }
}

function connect() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${window.location.host}/ws`)
  ws.onmessage = (evt) => {
    let msg
    try { msg = JSON.parse(evt.data) } catch { return }
    if (msg.type === 'display.presentation') {
      applyPresentation(msg.enabled)
      return
    }
    // Darts coming out of the board ends the turn. The board flashes red for
    // the same half second; this is the matching cue for whoever is looking
    // at the screen rather than the board.
    if (msg.type === 'detection.takeout') {
      playTakeoutSound()
      takingOut.value = true
      clearTimeout(takeoutTimer)
      takeoutTimer = setTimeout(() => { takingOut.value = false }, 1600)
      return
    }
    if (msg.type !== 'game.state') return
    const previous = state.value
    state.value = msg.state
    const before = previous.darts_this_turn?.length ?? 0
    const after = msg.state.darts_this_turn?.length ?? 0
    if (after > before) {
      const last = msg.state.history?.[0]
      if (last?.highlight === 'bad') playReviewSound()
      else playHitSound()
    }
    if (!msg.state.finished) winnerDismissed.value = false
  }
  ws.onclose = () => { ws = null; setTimeout(connect, 2000) }
}

async function act(fn) {
  busy.value = true
  try { state.value = await fn() } finally { busy.value = false }
}

const undo = () => act(api.undoDart)
// Two different jobs behind one button, so it has to pick the right endpoint.
//
// While the darts are still in the board it is the takeout confirmation, and
// confirmTakeout is the authority: it rewinds whatever detection did since the
// prompt appeared, then advances exactly once, landing correctly whether
// detection got it right, fired twice, or missed it entirely.
//
// Once detection has already advanced the turn, that same call rewinds to the
// prompt and re-advances - i.e. it lands back exactly where it started and
// looks broken. There it should simply advance the turn, which is nextTurn.
// Putting the turn *back* is `previousPlayer`, not this.
const dartsRemoved = () =>
  act(state.value.awaiting_takeout ? api.confirmTakeout : api.nextTurn)
// The recovery when the turn changed and should not have - a takeout that
// fired twice, or fired on a hand that was only reaching past the board.
// Rewinds to before the change, so the previous player gets their own darts
// back rather than a fresh turn.
const previousPlayer = () => act(api.previousTurn)
const recordMiss = () => { unlockAudio(); return act(() => api.sendManualDart(null, 0)) }

// Reset Autodarts' board detection - the same "Manual reset" its Board Manager
// fires. Clears the darts the cameras currently see so a mis-detected visit can
// be thrown again. This resets the *board*, not the InterDarts scoreboard; use
// Undo for a dart that was already scored here. Its own busy flag (not the
// shared `act`) because it returns a board result, not new game state.
const resetting = ref(false)
async function resetBoard() {
  if (resetting.value) return
  resetting.value = true
  try {
    await api.resetBoard()
  } catch (e) {
    console.warn('Autodarts board reset failed', e)
  } finally {
    resetting.value = false
  }
}

async function quit() {
  await api.stopGame()
  router.push('/games')
}

function parseTarget(target) {
  if (target === 'MISS') return { segment: null, multiplier: 0 }
  if (target === '25') return { segment: 25, multiplier: 1 }
  if (target === 'BULL') return { segment: 25, multiplier: 2 }
  const m = /^([SDT])(\d+)$/.exec(target)
  if (!m) return { segment: null, multiplier: 0 }
  return { segment: Number(m[2]), multiplier: { S: 1, D: 2, T: 3 }[m[1]] }
}

const overrideScore = computed(() => {
  const { segment, multiplier } = parseTarget(overrideTarget.value)
  return segment ? segment * multiplier : 0
})

function openOverride() {
  unlockAudio()
  const last = darts.value.at(-1)
  overrideAction.value = last ? 'replace' : 'add'
  overrideTarget.value = /^(?:[SDT](?:[1-9]|1\d|20)|25|BULL|MISS)$/.test(last?.label || '') ? last.label : 'MISS'
  overrideOpen.value = true
}

async function applyOverride() {
  const { segment, multiplier } = parseTarget(overrideTarget.value)
  busy.value = true
  try {
    if (overrideAction.value === 'replace' && darts.value.length) await api.undoDart()
    state.value = await api.sendManualDart(segment, multiplier)
    overrideOpen.value = false
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------- fullscreen
// Driven by an effect rather than by each event handler: the page can arrive
// *already* in fullscreen (the Games screen requests it on the Start click,
// while the click is still a user gesture, then routes here) and no
// fullscreenchange event fires during mount. Deriving the classes from state
// means every route in - navigation, a remote toggle, a game starting - lands
// in the right layout without a handler having to remember to sync.
watchEffect(() => {
  document.body.classList.toggle('fullscreen-game', isFullscreen.value && Boolean(state.value.active))
  document.body.classList.toggle('presentation-mode', presentation.value)
})

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await document.documentElement.requestFullscreen({ navigationUI: 'hide' })
  } catch { /* browser refused - the presentation-mode class still applies via remote */ }
}

function onFullscreenChange() {
  isFullscreen.value = Boolean(document.fullscreenElement)
}

function applyPresentation(enabled) {
  presentation.value = Boolean(enabled)
}

async function exitPresentation() {
  try { await api.setPresentation(false) } catch { /* transient */ }
  applyPresentation(false)
}

async function loadGeometry() {
  try { geometry.value = await api.getBoardGeometry() } catch { /* board is a nice-to-have */ }
}

async function loadDisplay() {
  try {
    const d = await api.getDisplay()
    applyPresentation(d.presentation)
  } catch { /* transient */ }
}

// ---------------------------------------------------------------- winner overlay
const showWinner = computed(() => state.value.active && state.value.finished && !winnerDismissed.value)
const confetti = computed(() => {
  const colors = ['#ffd84f', '#ff4fcf', '#54e6ff', '#9cff55', '#ff704d', '#ffffff']
  return Array.from({ length: 54 }, (_, i) => ({
    x: `${(i * 37 + 11) % 100}%`,
    delay: `${-((i * 0.23) % 5.4)}s`,
    duration: `${4.2 + (i % 7) * 0.37}s`,
    drift: `${(i % 2 ? 1 : -1) * (22 + (i % 6) * 9)}px`,
    spin: `${420 + (i % 5) * 170}deg`,
    color: colors[i % colors.length],
  }))
})
const winnerLabel = computed(() => {
  if (kind.value === 'invaders') {
    if (game.value.result === 'defeat') return 'SPACE INVADERS · MISSION LOST'
    return 'SPACE INVADERS · FLEET CLEARED'
  }
  if (kind.value === 'killer') return 'LAST PLAYER ALIVE'
  if (kind.value === 'derby') return 'DONKEY DERBY · FIRST PAST THE POST'
  if (kind.value === 'snakes') return 'SNAKES & LADDERS · FIRST TO 100'
  if (kind.value === 'x01') return 'X01 CHAMPION'
  return (state.value.name || 'GAME').toUpperCase() + ' CHAMPION'
})
const spaceDefeat = computed(() => kind.value === 'invaders' && game.value.result === 'defeat')

onMounted(() => {
  refresh()
  loadGeometry()
  loadDisplay()
  connect()
  poll = setInterval(refresh, 3000)
  document.addEventListener('fullscreenchange', onFullscreenChange)
  onFullscreenChange()   // we may have arrived already fullscreen
})
onBeforeUnmount(() => {
  ws?.close()
  if (poll) clearInterval(poll)
  clearTimeout(takeoutTimer)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.body.classList.remove('fullscreen-game', 'presentation-mode')
})
</script>

<template>
  <div v-if="!state.active" class="empty">
    <h1>No game running</h1>
    <p class="muted">Pick something from the <router-link to="/games">Games</router-link> library.</p>
  </div>

  <div v-else class="arena-page">
    <!-- titlebar -->
    <div class="arena-titlebar">
      <div class="arena-title">
        <p class="arena-eyebrow">NOW PLAYING</p>
        <h1>{{ title }}</h1>
      </div>
      <div class="arena-title-actions">
        <div class="scan-status" :class="scan.tone">
          <i></i>
          <div><strong>{{ scan.label }}</strong><span>{{ scan.detail }}</span></div>
        </div>
        <div class="arena-buttons">
          <button
            class="abtn secondary"
            :disabled="resetting"
            title="Clear what the cameras currently see, to re-throw a mis-detected visit (same as Autodarts' Manual reset)"
            @click="resetBoard"
          >{{ resetting ? 'Resetting…' : 'Reset board' }}</button>
          <button class="abtn secondary" @click="toggleFullscreen">{{ isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen' }}</button>
          <button class="abtn secondary" @click="router.push('/games')">Change game</button>
          <button class="abtn secondary" @click="quit">End game</button>
        </div>
      </div>
    </div>

    <!-- ======================================================= KILLER -->
    <div v-if="kind === 'killer'" class="game-panel mode-killer">
      <div class="arena-layout killer-layout">
        <article class="arena-panel killer-current">
          <span class="arena-ribbon">UP NOW</span>
          <img class="player-photo large-photo" :src="avatarFor(current)" alt="" />
          <strong class="killer-name">{{ current?.name }}</strong>
          <div class="killer-assignment">
            <b>{{ killerTargets(current?.player_id) }}</b>
            <span>{{ difficultyName.toUpperCase() }} TARGETS</span>
          </div>
          <div class="killer-objective">
            <small>YOUR OBJECTIVE</small>
            <strong>{{ killerObjective }}</strong>
          </div>
          <div class="killer-marks">
            <span
              v-for="i in game.marks_to_kill ?? 3"
              :key="i"
              :class="{ earned: i <= killerMarks(current?.player_id) }"
            >{{ isKiller(current?.player_id) ? '☠' : '●' }}</span>
          </div>
          <div class="killer-lives">
            <span v-for="i in game.max_lives ?? 3" :key="i" :class="{ alive: i <= (current?.score ?? 0) }">☠</span>
          </div>
        </article>

        <article class="killer-target">
          <div class="killer-round">{{ difficultyName.toUpperCase() }} · ROUND {{ state.round }} OF THE HUNT</div>
          <div class="stage-board killer-board">
            <DartboardFace
              v-if="geometry"
              :geometry="geometry"
              fluid
              theme="killer"
              :highlight="state.highlight || []"
              :darts="darts"
            />
          </div>
          <div class="killer-turn-track">
            <span v-for="i in state.darts_per_turn" :key="i" :class="{ hit: darts[i - 1] }">
              {{ darts[i - 1]?.label ?? '☠' }}
            </span>
          </div>
          <TakeoutPrompt
            :state="state"
            :busy="busy"
            :next-player-name="nextPlayerName"
            @confirm="dartsRemoved"
            @undo="undo"
            @miss="recordMiss"
            @previous="previousPlayer"
            @override="openOverride"
          />
        </article>

        <section class="arena-panel side-panel">
          <span class="arena-ribbon">UP NEXT</span>
          <div class="killer-players">
            <article
              v-for="(p, index) in players"
              :key="p.player_id"
              class="killer-player"
              :class="{ current: p.player_id === state.current_player_id, eliminated: p.finished && !state.finished }"
              :style="{ '--player-accent': accent(index) }"
            >
              <b class="killer-number">{{ killerTargets(p.player_id) }}</b>
              <img class="player-photo" :src="avatarFor(p)" alt="" />
              <div>
                <strong>{{ p.name }}</strong>
                <span>{{ isKiller(p.player_id) ? 'KILLER' : `${killerMarks(p.player_id)}/${game.marks_to_kill ?? 3} marks` }} · {{ killerTargets(p.player_id) }}</span>
              </div>
              <i><em v-for="i in game.max_lives ?? 3" :key="i" :class="{ alive: i <= p.score }">☠</em></i>
            </article>
          </div>
          <div class="embedded-turn">
            <div class="turn-heading">
              <div><small>CURRENT TURN</small><strong>{{ current?.name }} · dart {{ dartNumber }} of {{ state.darts_per_turn }}</strong></div>
              <span class="game-phase" :class="{ warn: state.awaiting_takeout }">{{ state.finished ? 'FINISHED' : state.awaiting_takeout ? 'REMOVE DARTS' : 'PLAYING' }}</span>
            </div>
            <div class="game-darts">
              <div v-for="i in state.darts_per_turn" :key="i" class="game-dart" :class="{ empty: !darts[i - 1] }">
                <strong v-if="darts[i - 1]">{{ darts[i - 1].label }}</strong>
                <span v-if="darts[i - 1]">{{ darts[i - 1].score }} points</span>
                <template v-if="!darts[i - 1]">D{{ i }} —</template>
              </div>
            </div>
          </div>
        </section>

        <aside class="arena-panel arena-rules killer-rules">
          <div class="arena-rules-content">
            <h2 class="killer-logo" aria-label="Killer">
              <svg viewBox="0 0 320 105" role="img" aria-hidden="true">
                <defs>
                  <path id="killer-title-curve" d="M 24 78 Q 160 10 296 78" />
                  <linearGradient id="killer-title-gradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stop-color="#fff4ff" /><stop offset=".26" stop-color="#ff79ff" />
                    <stop offset=".62" stop-color="#e21dff" /><stop offset="1" stop-color="#7d0ba7" />
                  </linearGradient>
                </defs>
                <text class="killer-logo-shadow"><textPath href="#killer-title-curve" startOffset="50%">KILLER</textPath></text>
                <text class="killer-logo-text"><textPath href="#killer-title-curve" startOffset="50%">KILLER</textPath></text>
              </svg>
            </h2>
            <p><strong>Targets are assigned automatically</strong> when the game starts.</p>
            <p><strong>{{ difficultyName }} highlights {{ { 1: 'one target slice', 2: 'two adjacent target slices', 3: 'three adjacent target slices' }[game.slices ?? 1] }}</strong> for every player.</p>
            <p><strong>Hit any of your highlighted slices three marks</strong> to become a Killer.</p>
            <p>Then hit any highlighted opponent slice to eliminate them.</p>
            <p>Doubles and trebles count as multiple hits.</p>
            <p>The last player left alive wins the game.</p>
          </div>
          <div class="embedded-controls">
            <div class="game-message">{{ state.message || state.target_hint || '' }}</div>
            <div class="action-grid">
              <button class="abtn miss-button" :disabled="busy" @click="openOverride">Override / miss</button>
              <button class="abtn complete-miss-button" :disabled="busy || state.finished" @click="recordMiss">Record complete miss</button>
              <button class="abtn secondary" :disabled="busy || !darts.length" @click="undo">Undo dart ({{ darts.length }}/{{ state.darts_per_turn ?? 3 }})</button>
              <button class="abtn secondary" :disabled="busy" @click="previousPlayer">Previous player</button>
              <button class="abtn takeout" :disabled="busy || state.finished" @click="dartsRemoved">{{ state.awaiting_takeout ? 'Darts removed' : 'Next player' }}</button>
              <button class="round-menu" aria-label="Game help" @click="helpOpen = true">?</button>
            </div>
          </div>
        </aside>
      </div>
    </div>

    <!-- ======================================================= SPACE INVADERS -->
    <div v-else-if="kind === 'invaders'" class="game-panel mode-space">
      <div class="arena-layout space-layout">
        <article class="arena-panel space-current">
          <span class="arena-ribbon">UP NOW</span>
          <div class="space-pilot-frame">
            <img class="player-photo large-photo" :src="avatarFor(current)" alt="" />
            <i aria-hidden="true"></i>
          </div>
          <strong class="space-name">{{ current?.name }}</strong>
          <div class="space-score-grid">
            <div><small>SCORE</small><b>{{ current?.score ?? 0 }}</b></div>
            <div><small>ALIENS DESTROYED</small><b>{{ invKills(current?.player_id) }}</b></div>
          </div>
          <div class="space-reactor">
            <div><small>MULTI-CANNON</small><strong :class="{ ready: cannonArmed }">{{ cannonArmed ? 'READY' : 'CHARGING' }}</strong></div>
            <div class="space-cannon-meter" :class="{ ready: cannonArmed }">
              <i :class="{ charged: cannonArmed }"></i><i :class="{ charged: cannonArmed }"></i><i :class="{ charged: cannonArmed }"></i>
            </div>
          </div>
          <div class="space-objective">
            <small>MISSION OBJECTIVE</small>
            <strong>{{ cannonArmed ? 'MULTI-CANNON READY — hit a numbered lane to fire left, centre and right' : `Destroy the ${difficultyName.toLowerCase()} fleet before it breaches the defence grid` }}</strong>
          </div>
          <div class="space-defence">
            <div class="space-defence-heading">
              <span><small>DEFENCE GRID</small><b>{{ invLives <= 1 ? 'CRITICAL' : invLives === 2 ? 'DAMAGED' : 'ONLINE' }}</b></span>
              <div class="space-shields"><i v-for="i in 3" :key="i" :class="i <= invLives ? 'active' : 'lost'"></i></div>
            </div>
            <div class="space-threat">
              <span><small>FLEET REMAINING</small><b>{{ fleetPercent }}%</b></span>
              <div><i :style="{ width: fleetPercent + '%' }"></i></div>
            </div>
            <div class="space-telemetry">
              <span><small>ROUND PROGRESS</small><b>{{ Math.min(players.length, currentIndex + 1) }} / {{ players.length }} PILOTS</b></span>
              <span><small>THREAT LEVEL</small><b>{{ difficultyName.toUpperCase() }}</b></span>
            </div>
          </div>
          <div class="space-missile-bays">
            <span v-for="i in state.darts_per_turn" :key="i" :class="darts[i - 1] ? 'spent' : 'loaded'">
              <i></i><b>{{ darts[i - 1]?.label ?? `D${i}` }}</b>
            </span>
          </div>
        </article>

        <article class="space-playfield">
          <div class="space-strip">
            <span><small>ROUND</small><b>{{ invRoundLabel }}</b></span>
            <span><small>INVADERS</small><b>{{ invRemaining }}</b></span>
            <span><small>DEFENCE LIVES</small><b>{{ invLives }}</b></span>
          </div>
          <SpaceStage :view="game" :darts="darts" :geometry="geometry" />
          <div class="space-turn-track">
            <span v-for="i in state.darts_per_turn" :key="i" :class="{ fired: darts[i - 1] }">
              {{ darts[i - 1]?.label ?? 'READY' }}
            </span>
          </div>
          <TakeoutPrompt
            :state="state"
            :busy="busy"
            :next-player-name="nextPlayerName"
            @confirm="dartsRemoved"
            @undo="undo"
            @miss="recordMiss"
            @previous="previousPlayer"
            @override="openOverride"
          />
        </article>

        <section class="arena-panel side-panel space-side">
          <span class="arena-ribbon">FLIGHT SQUAD</span>
          <div class="space-players">
            <article
              v-for="(p, index) in players"
              :key="p.player_id"
              class="space-player"
              :class="{ current: p.player_id === state.current_player_id }"
              :style="{ '--player-accent': accent(index) }"
            >
              <span class="space-rank">{{ index + 1 }}</span>
              <img class="player-photo" :src="avatarFor(p)" alt="" />
              <div>
                <strong>{{ p.name }}</strong>
                <span>{{ game.cannons?.[p.player_id] ? 'MULTI READY' : p.player_id === state.current_player_id ? 'FIRING NOW' : 'FLIGHT SQUAD' }}</span>
              </div>
              <b>{{ p.score ?? 0 }}<small>PTS</small></b>
              <i>{{ invKills(p.player_id) }}<small>KILLS</small></i>
            </article>
          </div>
          <div class="space-fleet-summary" :style="{ '--fleet-angle': fleetPercent * 3.6 + 'deg' }">
            <div class="space-fleet-gauge"><strong>{{ invRemaining }}</strong><small>HOSTILES</small></div>
            <div>
              <small>FLEET TELEMETRY</small>
              <b>{{ invLeader ? `${invLeader.name} LEADS · ${invLeader.score ?? 0} PTS` : 'NO SCORE YET' }}</b>
              <span>{{ state.message || state.target_hint || 'Fire into the highlighted lanes' }}</span>
            </div>
          </div>
          <div class="embedded-turn">
            <div class="turn-heading">
              <div><small>CURRENT TURN</small><strong>{{ current?.name }} · dart {{ dartNumber }} of {{ state.darts_per_turn }}</strong></div>
              <span class="game-phase" :class="{ warn: state.awaiting_takeout }">{{ state.finished ? 'FINISHED' : state.awaiting_takeout ? 'REMOVE DARTS' : 'PLAYING' }}</span>
            </div>
          </div>
        </section>

        <aside class="arena-panel arena-rules space-rules">
          <div class="arena-rules-content">
            <div class="space-logo" aria-label="Space Invaders">
              <small>ORBITAL DEFENCE</small><strong>SPACE</strong><b>INVADERS</b><i aria-hidden="true"></i>
            </div>
            <p>Hit a numbered sector to fire into that alien lane.</p>
            <p><strong>Doubles and trebles fire two or three shots.</strong></p>
            <p><strong>Outer bull arms Multi-Cannon:</strong> your next numbered hit also fires left and right.</p>
            <p><strong>Inner bull fires across every numbered lane.</strong></p>
            <p>Aliens advance after each round. Three advances cost one defence life.</p>
            <p>Clear the fleet before all three defence lives are lost.</p>
          </div>
          <div class="embedded-controls">
            <div class="game-message">{{ state.message || '' }}</div>
            <div class="action-grid">
              <button class="abtn miss-button" :disabled="busy" @click="openOverride">Override / miss</button>
              <button class="abtn complete-miss-button" :disabled="busy || state.finished" @click="recordMiss">Record complete miss</button>
              <button class="abtn secondary" :disabled="busy || !darts.length" @click="undo">Undo dart ({{ darts.length }}/{{ state.darts_per_turn ?? 3 }})</button>
              <button class="abtn secondary" :disabled="busy" @click="previousPlayer">Previous player</button>
              <button class="abtn takeout" :disabled="busy || state.finished" @click="dartsRemoved">{{ state.awaiting_takeout ? 'Darts removed' : 'Next player' }}</button>
              <button class="round-menu" aria-label="Game help" @click="helpOpen = true">?</button>
            </div>
          </div>
        </aside>
      </div>
    </div>

    <!-- ======================================================= DARTS GOLF -->
    <div v-else-if="kind === 'golf'" class="game-panel mode-golf">
      <div class="arena-layout golf-layout">
        <article class="arena-panel golf-current">
          <span class="arena-ribbon">ON THE TEE</span>

          <div class="golf-flagbox">
            <svg viewBox="0 0 120 100" aria-hidden="true">
              <path d="M0 100 Q34 74 60 78 Q92 83 120 66 L120 100 Z" fill="#1c4023" />
              <ellipse cx="62" cy="82" rx="40" ry="12" fill="#2f7a3c" />
              <ellipse cx="62" cy="82" rx="9" ry="3.4" fill="#061007" />
              <rect x="60" y="24" width="2.4" height="58" fill="#f4f1e4" />
              <path d="M62.4 25 L96 33 L62.4 42 Z" fill="#f2c14e" />
            </svg>
            <div class="golf-flagnum">
              <small>HOLE</small>
              <strong>{{ game.hole }}</strong>
              <em>of {{ game.holes }}</em>
            </div>
          </div>

          <div class="golf-identity">
            <img class="player-photo golf-photo" :src="avatarFor(current)" alt="" />
            <strong>{{ current?.name }}</strong>
          </div>

          <div class="golf-strokes">
            <small>STROKES</small>
            <b>{{ current?.score ?? 0 }}</b>
            <em>{{ golfToPar(current) }}</em>
          </div>

          <div class="golf-balls">
            <span v-for="i in state.darts_per_turn" :key="i" :class="{ played: darts[i - 1] }">
              <i></i><b>{{ darts[i - 1]?.label ?? '—' }}</b>
            </span>
          </div>

          <div class="golf-payout">
            <span><i class="ace"></i>TREBLE<b>1</b></span>
            <span><i class="birdie"></i>DOUBLE<b>2</b></span>
            <span><i class="par"></i>SINGLE<b>{{ game.par ?? 3 }}</b></span>
            <span><i class="bogey"></i>MISS<b>{{ game.bogey ?? 5 }}</b></span>
          </div>
        </article>

        <article class="golf-green">
          <div class="golf-strip">
            <span><small>HOLE</small><b>{{ game.hole }} / {{ game.holes }}</b></span>
            <span><small>PLAYING</small><b>{{ current?.name }}</b></span>
            <span><small>LEADER</small><b>{{ golfLeader?.name ?? '—' }}</b></span>
          </div>
          <div class="golf-board">
            <DartboardFace
              v-if="geometry"
              :geometry="geometry"
              fluid
              :theme="state.theme || 'golf'"
              :highlight="state.highlight || []"
              :darts="darts"
            />
          </div>
          <div class="golf-lastshot">
            <small>LAST SHOT</small><strong>{{ darts.at(-1)?.label ?? 'ADDRESSING THE BALL' }}</strong>
          </div>
          <TakeoutPrompt
            :state="state"
            :busy="busy"
            :next-player-name="nextPlayerName"
            @confirm="dartsRemoved"
            @undo="undo"
            @miss="recordMiss"
            @previous="previousPlayer"
            @override="openOverride"
          />
        </article>

        <section class="arena-panel side-panel golf-side">
          <span class="arena-ribbon">CLUBHOUSE</span>
          <GolfScorecard :view="game" :players="players" :current-id="state.current_player_id" />
        </section>

        <aside class="arena-panel arena-rules golf-rules">
          <div class="arena-rules-content">
            <div class="golf-logo">
              <span>THE COURSE</span><strong>GOLF</strong><small>{{ game.holes }} HOLES · LOWEST WINS</small>
            </div>
            <p>Play the holes in order — everyone plays hole {{ game.hole }}, then the course moves on.</p>
            <p>Three darts at each hole, but the first one to find it finishes the hole.</p>
            <p v-if="game.strict"><strong>Championship course:</strong> a single only gets you {{ game.par }}, and a missed hole costs {{ game.bogey }}.</p>
            <p>Detected darts enter automatically. Override any result from the controls below.</p>
          </div>
          <div class="embedded-controls">
            <div class="game-message">{{ state.message || state.target_hint || '' }}</div>
            <div class="action-grid">
              <button class="abtn miss-button" :disabled="busy" @click="openOverride">Override / miss</button>
              <button class="abtn complete-miss-button" :disabled="busy || state.finished" @click="recordMiss">Record complete miss</button>
              <button class="abtn secondary" :disabled="busy || !darts.length" @click="undo">Undo dart ({{ darts.length }}/{{ state.darts_per_turn ?? 3 }})</button>
              <button class="abtn secondary" :disabled="busy" @click="previousPlayer">Previous player</button>
              <button class="abtn takeout" :disabled="busy || state.finished" @click="dartsRemoved">{{ state.awaiting_takeout ? 'Darts removed' : 'Next player' }}</button>
              <button class="round-menu" aria-label="Game help" @click="helpOpen = true">?</button>
            </div>
          </div>
        </aside>
      </div>
    </div>

    <!-- ======================================================= NOUGHTS & CROSSES -->
    <div v-else-if="kind === 'oxo'" class="game-panel mode-oxo">
      <div class="arena-layout oxo-layout">
        <article class="arena-panel oxo-current">
          <span class="arena-ribbon">UP NOW</span>
          <div class="oxo-bigmark" :class="oxoSymbol === 'X' ? 'x' : 'o'">{{ oxoSymbol }}</div>
          <div class="oxo-identity">
            <img class="player-photo oxo-photo" :class="oxoSymbol === 'X' ? 'x' : 'o'" :src="avatarFor(current)" alt="" />
            <strong>{{ current?.name }}</strong>
          </div>
          <div class="oxo-darts">
            <span v-for="i in state.darts_per_turn" :key="i" :class="{ thrown: darts[i - 1] }">
              {{ darts[i - 1]?.label ?? '◆' }}
            </span>
          </div>
          <div class="oxo-owned">
            <small>SQUARES OWNED</small>
            <b>{{ current?.score ?? 0 }}</b>
          </div>
          <div v-if="state.target_hint" class="oxo-hintbox">{{ state.target_hint }}</div>
        </article>

        <article class="oxo-stage">
          <div class="oxo-play">
            <div class="oxo-grid">
              <div
                v-for="(sq, i) in game.squares ?? []"
                :key="i"
                class="oxo-cell"
                :class="[
                  sq.owner === 'X' ? 'x' : sq.owner === 'O' ? 'o' : 'open',
                  { win: (game.winning_line ?? []).includes(i), aim: !sq.owner && i === oxoSuggested && !state.finished },
                ]"
              >
                <small>{{ sq.label }}</small>
                <strong v-if="sq.owner">{{ sq.owner }}</strong>
                <em v-else-if="i === oxoSuggested && !state.finished">AIM HERE</em>
              </div>
            </div>
            <div class="oxo-boardcol">
              <DartboardFace
                v-if="geometry"
                :geometry="geometry"
                fluid
                :theme="state.theme || 'classic'"
                :highlight="state.highlight || []"
                :darts="darts"
              />
              <div class="oxo-lastdart">
                <small>LAST DART</small><strong>{{ darts.at(-1)?.label ?? 'WAITING' }}</strong>
              </div>
            </div>
          </div>
          <TakeoutPrompt
            :state="state"
            :busy="busy"
            :next-player-name="nextPlayerName"
            @confirm="dartsRemoved"
            @undo="undo"
            @miss="recordMiss"
            @previous="previousPlayer"
            @override="openOverride"
          />
        </article>

        <section class="arena-panel side-panel oxo-side">
          <span class="arena-ribbon">HEAD TO HEAD</span>
          <div class="oxo-versus">
            <article
              v-for="p in players"
              :key="p.player_id"
              class="oxo-player"
              :class="[game.symbols?.[p.player_id] === 'X' ? 'x' : 'o', { current: p.player_id === state.current_player_id }]"
            >
              <img :src="avatarFor(p)" alt="" />
              <div>
                <strong>{{ p.name }}</strong>
                <small>{{ p.score ?? 0 }} square{{ (p.score ?? 0) === 1 ? '' : 's' }}</small>
              </div>
              <b>{{ game.symbols?.[p.player_id] }}</b>
            </article>
            <div class="oxo-vs" aria-hidden="true">VS</div>
          </div>
        </section>

        <aside class="arena-panel arena-rules oxo-rules">
          <div class="arena-rules-content">
            <div class="oxo-logo">
              <span>THREE IN A ROW</span>
              <strong><i class="x">X</i>·<i class="o">O</i></strong>
              <small>NOUGHTS &amp; CROSSES</small>
            </div>
            <p>Each square belongs to a board number - hit it to claim the square. Claimed squares are locked.</p>
            <p v-if="game.end_turn_after_claim"><strong>Standard rules:</strong> a successful claim ends your turn.</p>
            <p v-if="game.claim === 'double_or_treble'"><strong>Hard rules:</strong> only doubles and trebles claim, and the centre needs the inner bull.</p>
            <p>Three of your marks in a line - across, down or diagonal - wins the game.</p>
            <p>Detected darts enter automatically. Override any result from the controls below.</p>
          </div>
          <div class="embedded-controls">
            <div class="game-message">{{ state.message || state.target_hint || '' }}</div>
            <div class="action-grid">
              <button class="abtn miss-button" :disabled="busy" @click="openOverride">Override / miss</button>
              <button class="abtn complete-miss-button" :disabled="busy || state.finished" @click="recordMiss">Record complete miss</button>
              <button class="abtn secondary" :disabled="busy || !darts.length" @click="undo">Undo dart ({{ darts.length }}/{{ state.darts_per_turn ?? 3 }})</button>
              <button class="abtn secondary" :disabled="busy" @click="previousPlayer">Previous player</button>
              <button class="abtn takeout" :disabled="busy || state.finished" @click="dartsRemoved">{{ state.awaiting_takeout ? 'Darts removed' : 'Next player' }}</button>
              <button class="round-menu" aria-label="Game help" @click="helpOpen = true">?</button>
            </div>
          </div>
        </aside>
      </div>
    </div>

    <!-- ======================================================= DONKEY DERBY -->
    <div v-else-if="kind === 'derby'" class="game-panel mode-derby">
      <div class="derby-live-layout">
        <DerbyRace
          :players="derbyPlayers"
          :numbers="game.numbers || {}"
          :track="game.track || 12"
          :current-player-id="state.current_player_id"
          :winner-id="state.winner_id"
          :round="state.round || 1"
        />

        <aside class="derby-console">
          <div class="derby-console-title">
            <span>PADDOCK CONTROL</span>
            <b :class="{ live: !state.finished }">{{ state.finished ? 'RESULT' : 'RACE LIVE' }}</b>
          </div>

          <section class="derby-current">
            <span class="derby-up-now">UP NOW</span>
            <div class="derby-rider">
              <img :src="avatarFor(current)" alt="" />
              <div><small>JOCKEY</small><strong>{{ current?.name }}</strong></div>
            </div>
            <div class="derby-aim">
              <div><small>YOUR NUMBER</small><strong>{{ derbyNumber(current?.player_id) }}</strong></div>
              <p>Hit it to race <b>forward</b></p>
            </div>
            <div class="derby-objective">Or hit a rival's number to knock their donkey backwards.</div>
          </section>

          <section class="derby-stable">
            <div class="derby-section-head"><span>THE FIELD</span><small>TARGET NUMBERS</small></div>
            <div class="derby-target-grid">
              <article
                v-for="(player, index) in players"
                :key="player.player_id"
                :class="{ current: player.player_id === state.current_player_id, winner: player.player_id === state.winner_id }"
                :style="{ '--player-accent': accent(index) }"
              >
                <img :src="avatarFor(player)" alt="" />
                <div><strong>{{ player.name }}</strong><small>{{ player.score }}/{{ game.track }}</small></div>
                <b>{{ derbyNumber(player.player_id) }}</b>
              </article>
            </div>
          </section>

          <section class="derby-visit">
            <div class="derby-section-head">
              <span>THIS VISIT</span>
              <small>DART {{ dartNumber }} OF {{ state.darts_per_turn }}</small>
            </div>
            <div class="derby-darts">
              <div v-for="i in state.darts_per_turn" :key="i" :class="{ scored: darts[i - 1] }">
                <strong>{{ darts[i - 1]?.label ?? `D${i}` }}</strong>
                <span>{{ darts[i - 1] ? derbyDartEffect(darts[i - 1]) : 'READY' }}</span>
              </div>
            </div>
          </section>

          <div class="derby-message">{{ state.message || state.target_hint || 'The runners are under starter’s orders…' }}</div>

          <div class="derby-actions action-grid">
            <button class="abtn miss-button" :disabled="busy" @click="openOverride">Override / miss</button>
            <button class="abtn complete-miss-button" :disabled="busy || state.finished" @click="recordMiss">Complete miss</button>
            <button class="abtn secondary" :disabled="busy || !darts.length" @click="undo">Undo dart</button>
            <button class="abtn secondary" :disabled="busy" @click="previousPlayer">Previous player</button>
            <button class="abtn takeout" :disabled="busy || state.finished" @click="dartsRemoved">{{ state.awaiting_takeout ? 'Darts removed' : 'Next jockey' }}</button>
            <button class="round-menu" aria-label="Game help" @click="helpOpen = true">Race help</button>
          </div>
        </aside>

        <TakeoutPrompt
          :state="state"
          :busy="busy"
          :next-player-name="nextPlayerName"
          @confirm="dartsRemoved"
          @undo="undo"
          @miss="recordMiss"
          @previous="previousPlayer"
          @override="openOverride"
        />
      </div>
    </div>

    <!-- ======================================================= MR vs MRS -->
    <div v-else-if="kind === 'chores'" class="game-panel mode-chores">
      <div class="chore-live-layout">
        <ChoreArena
          :players="chorePlayers"
          :game="game"
          :darts="darts"
          :geometry="geometry"
          :highlight="state.highlight || []"
          :finished="Boolean(state.finished)"
          :winner-id="state.winner_id"
          :message="state.message"
        />

        <aside class="chore-console">
          <div class="chore-console-title">
            <span>CHORE CHALLENGE</span>
            <b :class="{ live: !state.finished }">{{ state.finished ? 'RESULT' : `ROUND ${game.chore_round} / ${game.rounds}` }}</b>
          </div>

          <section class="chore-up-now">
            <span class="chore-eyebrow">{{ state.finished ? 'FINAL SCORE' : 'THROWING NOW' }}</span>
            <div class="chore-rider">
              <img :src="avatarFor(choreThrower)" alt="" />
              <div>
                <small>ONE DART EACH</small>
                <strong>{{ state.finished ? 'Game over' : choreThrower?.name }}</strong>
              </div>
            </div>
            <p class="chore-objective">{{ state.target_hint || 'Highest score wins the round and avoids the chore.' }}</p>
          </section>

          <section class="chore-visit">
            <div class="chore-section-head">
              <span>THIS ROUND</span>
              <small>DART {{ dartNumber }} OF {{ state.darts_per_turn }}</small>
            </div>
            <div class="chore-darts">
              <div v-for="i in state.darts_per_turn" :key="i" :class="{ scored: darts[i - 1] }">
                <strong>{{ darts[i - 1]?.label ?? `D${i}` }}</strong>
                <span>{{ darts[i - 1] ? `${darts[i - 1].score} pts` : 'READY' }}</span>
              </div>
            </div>
          </section>

          <div class="chore-actions action-grid">
            <button class="abtn miss-button" :disabled="busy" @click="openOverride">Override / miss</button>
            <button class="abtn complete-miss-button" :disabled="busy || state.finished" @click="recordMiss">Complete miss</button>
            <button class="abtn secondary" :disabled="busy || !darts.length" @click="undo">Undo dart</button>
            <button class="abtn secondary" :disabled="busy" @click="previousPlayer">Previous round</button>
            <button class="abtn takeout" :disabled="busy || state.finished" @click="dartsRemoved">{{ state.awaiting_takeout ? 'Darts removed' : 'Next round' }}</button>
            <button class="round-menu" aria-label="Game help" @click="helpOpen = true">Chore help</button>
          </div>
        </aside>

        <TakeoutPrompt
          :state="state"
          :busy="busy"
          :next-player-name="nextPlayerName"
          @confirm="dartsRemoved"
          @undo="undo"
          @miss="recordMiss"
          @previous="previousPlayer"
          @override="openOverride"
        />
      </div>
    </div>

    <!-- ======================================================= SNAKES & LADDERS -->
    <div v-else-if="kind === 'snakes'" class="game-panel mode-snakes">
      <div class="snl-live-layout">
        <div class="snl-stage">
          <SnakesBoard
            :players="snakePlayers"
            :positions="game.positions || {}"
            :ladders="game.ladders || {}"
            :snakes="game.snakes || {}"
            :finish="game.finish || 100"
            :columns="game.columns || 10"
            :current-player-id="state.current_player_id"
            :winner-id="state.winner_id"
            :last-move="game.last_move"
            :dart-number="dartNumber"
            :darts-per-turn="state.darts_per_turn"
          />
        </div>

        <aside class="snl-console">
          <div class="snl-console-title">
            <span>BOARD CONTROL</span>
            <b :class="{ live: !state.finished }">{{ state.finished ? 'RESULT' : 'RACE LIVE' }}</b>
          </div>

          <section class="snl-current">
            <span class="snl-up-now">UP NOW</span>
            <div class="snl-rider">
              <img :src="avatarFor(current)" alt="" />
              <div><small>ON SQUARE</small><strong>{{ snakeSquare(current?.player_id) }}</strong></div>
            </div>
            <div class="snl-aim">
              <div><small>TO GO</small><strong>{{ (game.finish || 100) - snakeSquare(current?.player_id) }}</strong></div>
              <p>Each dart moves you <b>⌈score ÷ {{ game.divisor || 5 }}⌉</b> squares. Land exactly on {{ game.finish || 100 }}.</p>
            </div>
            <div class="snl-objective">Every dart moves and reacts · ladders climb · snakes slide · overshoot and you stay put.</div>
          </section>

          <section class="snl-field">
            <div class="snl-section-head"><span>THE FIELD</span><small>SQUARES</small></div>
            <div class="snl-target-grid">
              <article
                v-for="(player, index) in players"
                :key="player.player_id"
                :class="{ current: player.player_id === state.current_player_id, winner: player.player_id === state.winner_id }"
                :style="{ '--player-accent': accent(index) }"
              >
                <img :src="avatarFor(player)" alt="" />
                <div><strong>{{ player.name }}</strong><small>{{ (game.finish || 100) - snakeSquare(player.player_id) }} to go</small></div>
                <b>{{ snakeSquare(player.player_id) }}</b>
              </article>
            </div>
          </section>

          <section class="snl-visit">
            <div class="snl-section-head">
              <span>THIS VISIT</span>
              <small>DART {{ dartNumber }} OF {{ state.darts_per_turn }}</small>
            </div>
            <div class="snl-darts">
              <div v-for="i in state.darts_per_turn" :key="i" :class="{ scored: darts[i - 1] }">
                <strong>{{ darts[i - 1]?.label ?? `D${i}` }}</strong>
                <span>{{ darts[i - 1] ? `MOVE ${snakeDartMove(darts[i - 1])}` : 'READY' }}</span>
              </div>
            </div>
          </section>

          <div class="snl-message">{{ state.message || state.target_hint || 'Throw three darts to move your token.' }}</div>

          <div class="snl-actions action-grid">
            <button class="abtn miss-button" :disabled="busy" @click="openOverride">Override / miss</button>
            <button class="abtn complete-miss-button" :disabled="busy || state.finished" @click="recordMiss">Complete miss</button>
            <button class="abtn secondary" :disabled="busy || !darts.length" @click="undo">Undo dart</button>
            <button class="abtn secondary" :disabled="busy" @click="previousPlayer">Previous player</button>
            <button class="abtn takeout" :disabled="busy || state.finished" @click="dartsRemoved">{{ state.awaiting_takeout ? 'Darts removed' : 'Next player' }}</button>
            <button class="round-menu" aria-label="Game help" @click="helpOpen = true">Board help</button>
          </div>
        </aside>

        <TakeoutPrompt
          :state="state"
          :busy="busy"
          :next-player-name="nextPlayerName"
          @confirm="dartsRemoved"
          @undo="undo"
          @miss="recordMiss"
          @previous="previousPlayer"
          @override="openOverride"
        />
      </div>
    </div>

    <!-- ======================================================= X01 + everything else -->
    <div v-else class="game-panel mode-x01">
      <div class="arena-layout x01-layout">
        <article class="arena-panel x01-current">
          <span class="arena-ribbon">UP NOW</span>
          <div class="x01-score-block">
            <small>{{ kind === 'x01' ? 'REMAINING' : kind === 'clock' ? 'TARGET' : 'SCORE' }}</small>
            <strong>{{ kind === 'clock'
              ? (game.targets?.[current?.player_id] > 20 ? 'BULL' : game.targets?.[current?.player_id])
              : current?.score }}</strong>
          </div>
          <div class="x01-current-darts">
            <span v-for="i in state.darts_per_turn" :key="i" :class="{ filled: darts[i - 1] }">
              {{ darts[i - 1]?.label ?? '◆' }}
            </span>
          </div>
          <div class="x01-identity">
            <img class="player-photo x01-photo" :src="avatarFor(current)" alt="" />
            <strong>{{ current?.name }}</strong>
          </div>
        </article>

        <article class="x01-stage">
          <span class="arena-ribbon board-ribbon">LIVE BOARD</span>
          <div class="stage-board">
            <DartboardFace
              v-if="geometry"
              :geometry="geometry"
              fluid
              :theme="state.theme || 'classic'"
              :highlight="state.highlight || []"
              :darts="darts"
            />
          </div>
          <div class="x01-last-hit"><small>LAST DART</small><strong>{{ darts.at(-1)?.label ?? 'WAITING' }}</strong></div>
          <TakeoutPrompt
            :state="state"
            :busy="busy"
            :next-player-name="nextPlayerName"
            @confirm="dartsRemoved"
            @undo="undo"
            @miss="recordMiss"
            @previous="previousPlayer"
            @override="openOverride"
          />
        </article>

        <section class="arena-panel side-panel">
          <span class="arena-ribbon">UP NEXT</span>
          <div class="x01-player-list">
            <article
              v-for="(p, index) in players"
              :key="p.player_id"
              class="x01-player-row"
              :class="{ current: p.player_id === state.current_player_id, winner: p.player_id === state.winner_id }"
              :style="{ '--player-accent': accent(index) }"
            >
              <span class="x01-index">{{ index + 1 }}</span>
              <img class="player-photo" :src="avatarFor(p)" alt="" />
              <div>
                <small>{{ p.player_id === state.winner_id ? 'WINNER' : p.player_id === state.current_player_id ? 'UP NOW' : 'UP NEXT' }}</small>
                <strong>{{ p.name }}</strong>
              </div>
              <b>{{ kind === 'clock'
                ? (game.targets?.[p.player_id] > 20 ? 'BULL' : game.targets?.[p.player_id])
                : p.score }}</b>
            </article>
          </div>
          <div class="embedded-turn">
            <div class="turn-heading">
              <div><small>CURRENT TURN</small><strong>{{ current?.name }} · dart {{ dartNumber }} of {{ state.darts_per_turn }}</strong></div>
              <span class="game-phase" :class="{ warn: state.awaiting_takeout }">{{ state.finished ? 'FINISHED' : state.awaiting_takeout ? 'REMOVE DARTS' : 'PLAYING' }}</span>
            </div>
            <div class="game-darts">
              <div v-for="i in state.darts_per_turn" :key="i" class="game-dart" :class="{ empty: !darts[i - 1] }">
                <strong v-if="darts[i - 1]">{{ darts[i - 1].label }}</strong>
                <span v-if="darts[i - 1]">{{ darts[i - 1].score }} points</span>
                <template v-if="!darts[i - 1]">D{{ i }} —</template>
              </div>
            </div>
          </div>
        </section>

        <aside class="arena-panel arena-rules x01-rules">
          <div class="arena-rules-content">
            <div v-if="kind === 'x01'" class="x01-logo">
              <span>CLASSIC DARTS</span><strong>X01</strong><small>201 · 301 · 401 · 501 · 601 · 701</small>
            </div>
            <div v-else class="x01-logo">
              <span>{{ (state.name || '').toUpperCase() }}</span>
            </div>
            <template v-if="kind === 'x01'">
              <p>Start at your chosen X01 score and race down to exactly zero.</p>
              <p v-if="game.double_out">Finish the leg on a double. A bust restores your turn-start score.</p>
              <p v-else>Any dart can finish the leg. A bust restores your turn-start score.</p>
              <p>Detected darts enter automatically. Override any result from the controls below.</p>
            </template>
            <template v-else>
              <p v-if="state.target_hint">{{ state.target_hint }}</p>
              <p>Detected darts enter automatically. Override any result from the controls below.</p>
            </template>
          </div>
          <div class="embedded-controls">
            <div class="game-message">{{ state.message || state.target_hint || '' }}</div>
            <div class="action-grid">
              <button class="abtn miss-button" :disabled="busy" @click="openOverride">Override / miss</button>
              <button class="abtn complete-miss-button" :disabled="busy || state.finished" @click="recordMiss">Record complete miss</button>
              <button class="abtn secondary" :disabled="busy || !darts.length" @click="undo">Undo dart ({{ darts.length }}/{{ state.darts_per_turn ?? 3 }})</button>
              <button class="abtn secondary" :disabled="busy" @click="previousPlayer">Previous player</button>
              <button class="abtn takeout" :disabled="busy || state.finished" @click="dartsRemoved">{{ state.awaiting_takeout ? 'Darts removed' : 'Next player' }}</button>
              <button class="round-menu" aria-label="Game help" @click="helpOpen = true">?</button>
            </div>
          </div>
        </aside>
      </div>
    </div>

    <!-- winner celebration -->
    <div v-if="showWinner" class="winner-celebration" :class="{ 'space-defeat': spaceDefeat }">
      <div class="winner-confetti" aria-hidden="true">
        <i
          v-for="(c, i) in confetti"
          :key="i"
          :style="{ '--x': c.x, '--delay': c.delay, '--duration': c.duration, '--drift': c.drift, '--spin': c.spin, '--confetti': c.color }"
        ></i>
      </div>
      <div class="winner-panel">
        <div class="winner-crown" aria-hidden="true">{{ spaceDefeat ? '!' : '★' }}</div>
        <img v-if="!spaceDefeat && winner" class="winner-photo" :src="avatarFor(winner)" alt="" />
        <p class="winner-kicker">GAME COMPLETE</p>
        <!-- A game can finish with no winner at all (a drawn Noughts & Crosses
             grid) - that is a result, not a victory, so no name and no WINNER!! -->
        <strong class="winner-name">{{ spaceDefeat ? 'THE FLEET BROKE THROUGH' : winner?.name ?? 'THE GRID IS FULL' }}</strong>
        <h2>{{ spaceDefeat ? 'DEFENCE FAILED' : winner ? 'WINNER!!' : "IT'S A DRAW" }}</h2>
        <p class="winner-game-label">{{ winnerLabel }}</p>
        <ol class="winner-places">
          <li v-for="p in ranked" :key="p.player_id">{{ p.place ? `${p.place}. ` : '' }}{{ p.name }} — {{ p.score }}</li>
        </ol>
        <div class="winner-actions">
          <button class="abtn winner-primary" @click="quit">Back to games</button>
          <button class="abtn secondary" @click="winnerDismissed = true">Keep score on screen</button>
        </div>
      </div>
    </div>

    <!-- override dialog -->
    <div v-if="overrideOpen" class="dialog-backdrop" @click.self="overrideOpen = false">
      <div class="dialog">
        <button class="dialog-close" @click="overrideOpen = false">×</button>
        <p class="arena-eyebrow">CORRECT THROW</p>
        <h2>Override or record a miss</h2>
        <p class="dialog-intro">Replace the last recorded dart, or add a dart the cameras missed completely.</p>
        <div class="correction-actions">
          <button :class="{ active: overrideAction === 'replace' }" :disabled="!darts.length" @click="overrideAction = 'replace'">Override last dart</button>
          <button :class="{ active: overrideAction === 'add' }" @click="overrideAction = 'add'">Add missed dart</button>
        </div>
        <div class="picker-wrap">
          <div class="picker-copy"><small>CLICK WHERE THE DART LANDED</small><span>The selected scoring area will light up.</span></div>
          <BoardPicker :target="overrideTarget" @pick="overrideTarget = $event" />
        </div>
        <div class="correction-summary"><span>NEW RESULT</span><strong>{{ overrideTarget }} · {{ overrideScore }}</strong></div>
        <div class="dialog-footer">
          <button class="abtn secondary" @click="overrideOpen = false">Cancel</button>
          <button class="abtn" :disabled="busy" @click="applyOverride">{{ overrideAction === 'replace' ? 'Override last dart' : 'Add result' }}</button>
        </div>
      </div>
    </div>

    <!-- help dialog -->
    <div v-if="helpOpen" class="dialog-backdrop" @click.self="helpOpen = false">
      <div class="dialog">
        <button class="dialog-close" @click="helpOpen = false">×</button>
        <p class="arena-eyebrow">IN-GAME MENU</p>
        <template v-if="kind === 'derby'">
          <h2>How Donkey Derby works</h2>
          <p class="dialog-copy"><strong>Hit your assigned number</strong> to move your donkey towards the finish.</p>
          <p class="dialog-copy"><strong>Hit a rival's assigned number</strong> to push that donkey backwards towards the start.</p>
          <p class="dialog-copy">Singles move one step, doubles move two and trebles move three. Other numbers, bull and misses do not move any runner.</p>
          <p class="dialog-copy">The first donkey to reach the finish post wins the race.</p>
        </template>
        <template v-else-if="kind === 'chores'">
          <h2>How Mr vs Mrs works</h2>
          <p class="dialog-copy">A chore appears and <strong>both players throw one dart</strong>. The highest score wins the round and avoids it; the other player gets it for the week.</p>
          <p class="dialog-copy">Level scores are <strong>sudden death</strong> — both throw again for the same chore until someone wins it.</p>
          <p class="dialog-copy">🔥 <strong>Double Trouble</strong> rounds make the loser do that chore twice. A <strong>Lucky Target</strong> lights up a number — hit it and you win a bonus to settle between yourselves. A <strong>Steal Round</strong> lets the winner move a chore around.</p>
          <p class="dialog-copy">Both darts come out together at the end of the round. The player who loses the most rounds spins the <strong>Wheel of Misfortune</strong>, and the chore lists stay on screen at the end so you can photograph them.</p>
        </template>
        <template v-else-if="kind === 'snakes'">
          <h2>How Snakes &amp; Ladders works</h2>
          <p class="dialog-copy">Throw <strong>three darts</strong> — their combined score is how many squares your token moves (up to 60 a turn).</p>
          <p class="dialog-copy">Land on the <strong>foot of a ladder</strong> and you climb to the top. Land on a <strong>snake's head</strong> and you slide down to its tail.</p>
          <p class="dialog-copy">You must land <strong>exactly on 100</strong> — if your move would take you past it, you stay where you are and try again next turn.</p>
          <p class="dialog-copy">First token to reach square 100 wins the race.</p>
        </template>
        <template v-else>
          <h2>Missed dart or removing darts?</h2>
          <p class="dialog-copy"><strong>Record complete miss</strong> immediately consumes one zero-score dart when the cameras detect nothing.</p>
          <p class="dialog-copy"><strong>Override / miss</strong> replaces a detected result or adds a dart the cameras missed, via the virtual board.</p>
          <p class="dialog-copy">After the third dart, remove all darts normally; the cameras advance automatically. If takeout is not recognised, press <strong>Darts removed</strong>.</p>
        </template>
        <div class="dialog-footer"><button class="abtn" @click="helpOpen = false">Back to game</button></div>
      </div>
    </div>

    <!-- darts being removed: matches the board's red flash -->
    <div v-if="takingOut" class="takeout-banner">DARTS REMOVED</div>

    <!-- remote presentation restore -->
    <button v-if="presentation" class="presentation-exit" @click="exitPresentation">Restore screen</button>
  </div>
</template>

<!-- Fullscreen and presentation mode reach outside this component - they hide
     the App shell's topbar and unconstrain its .page wrapper - so those few
     rules have to be global. Everything else stays scoped. -->
<style>
body.presentation-mode,
body.fullscreen-game { overflow: hidden; }

body.presentation-mode .topbar,
body.fullscreen-game .topbar { display: none; }

body.presentation-mode .page { max-width: none; height: 100vh; padding: 10px 14px; overflow: hidden; }
body.fullscreen-game .page { max-width: none; height: 100vh; padding: 8px 14px; overflow: hidden; }
</style>

<style scoped>
.empty { text-align: center; padding: 3rem 1rem; }

.arena-page { --arena-cyan: #38d9f1; --arena-green: #57dc8b; --arena-amber: #ffbf4d; --arena-red: #ff5f69; }

.arena-eyebrow { margin: 0 0 4px; color: var(--arena-cyan, #38d9f1); font-size: 10px; font-weight: 900; letter-spacing: 0.17em; text-transform: uppercase; }

.arena-titlebar { min-height: 58px; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
.arena-title h1 { margin: 0; font-size: 26px; letter-spacing: -0.02em; }
.arena-title-actions { min-width: 0; flex: 1; display: flex; align-items: stretch; justify-content: flex-end; gap: 12px; flex-wrap: wrap; }
.arena-buttons { display: flex; gap: 8px; align-items: center; }

.abtn {
  border: 1px solid rgba(139, 169, 212, 0.22); border-radius: 7px; padding: 10px 14px;
  background: linear-gradient(155deg, #3479ed 0%, #6948dc 62%, #4b2da5 100%);
  color: white; font-weight: 800; font-size: 11px; letter-spacing: 0.02em; cursor: pointer;
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.2), 0 6px 16px rgba(0, 0, 0, 0.22);
  transition: transform 0.16s ease, filter 0.16s ease;
}
.abtn:hover:not(:disabled) { filter: brightness(1.13); transform: translateY(-1px); }
.abtn:disabled { opacity: 0.38; cursor: default; }
.abtn.secondary { border-color: rgba(119, 145, 181, 0.26); background: linear-gradient(160deg, rgba(28, 39, 55, 0.98), rgba(13, 19, 29, 0.98)); color: #dce3eb; }
.abtn.miss-button { border-color: #ff54c7; background: linear-gradient(180deg, #ed20a4, #8d0d68); }
.abtn.complete-miss-button { border-color: rgba(255, 79, 104, 0.75); background: linear-gradient(180deg, #ba3148, #681a2d); }
.abtn.takeout:not(:disabled) { border-color: #d9ff6b; background: linear-gradient(180deg, #baff19, #6eac08); color: #101800; }

.scan-status { min-width: 280px; min-height: 54px; padding: 10px 16px; flex: 1 1 auto; max-width: 640px; display: grid; grid-template-columns: 13px 1fr; gap: 12px; align-items: center; border: 1px solid rgba(87, 220, 139, 0.28); border-radius: 12px; background: rgba(11, 19, 24, 0.78); }
.scan-status i { width: 12px; height: 12px; border-radius: 50%; background: var(--arena-green); box-shadow: 0 0 16px var(--arena-green); animation: scan-pulse 1.7s ease-in-out infinite; }
.scan-status > div { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.scan-status strong { color: var(--arena-green); font-size: 12px; letter-spacing: 0.12em; }
.scan-status span { overflow: hidden; color: #93a0b2; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.scan-status.working { border-color: rgba(56, 217, 241, 0.35); }
.scan-status.working i { background: var(--arena-cyan); box-shadow: 0 0 13px var(--arena-cyan); }
.scan-status.working strong { color: var(--arena-cyan); }
.scan-status.warn { border-color: rgba(255, 191, 77, 0.42); }
.scan-status.warn i { background: var(--arena-amber); box-shadow: 0 0 13px var(--arena-amber); }
.scan-status.warn strong { color: var(--arena-amber); }
.scan-status.bad { border-color: rgba(255, 95, 105, 0.38); }
.scan-status.bad i { background: var(--arena-red); box-shadow: 0 0 13px var(--arena-red); animation: none; }
.scan-status.bad strong { color: var(--arena-red); }
@keyframes scan-pulse { 50% { opacity: 0.46; transform: scale(0.72); } }

.game-panel { border: 1px solid rgba(118, 144, 183, 0.27); border-radius: 18px; overflow: hidden; background: #080b11; box-shadow: 0 36px 110px rgba(0, 0, 0, 0.48); }

.arena-layout { min-height: 560px; padding: 13px; position: relative; display: grid; grid-template-columns: 215px minmax(380px, 1fr) 270px 300px; gap: 10px; isolation: isolate; overflow: hidden; background-position: center; background-size: cover; }
.arena-layout::before { position: absolute; inset: 0; z-index: -1; content: ''; pointer-events: none; }
.x01-layout { background-color: #10152a; background-image: url('/arenas/x01-arena.png'); }
.x01-layout::before { background: linear-gradient(90deg, rgba(18, 1, 36, 0.33), rgba(0, 10, 33, 0.08) 54%, rgba(1, 7, 17, 0.45)); }
.killer-layout { background-color: #09040f; background-image: url('/arenas/killer-arena.png'); }
.killer-layout::before { background: radial-gradient(circle at 43% 48%, rgba(28, 5, 49, 0.08) 0 21%, rgba(5, 1, 11, 0.54) 52%, rgba(4, 1, 9, 0.86) 100%), linear-gradient(90deg, rgba(13, 1, 24, 0.7), rgba(30, 1, 52, 0.18) 54%, rgba(5, 0, 11, 0.76)); }
.space-layout { min-height: 640px; padding: 10px; grid-template-columns: 195px minmax(460px, 1fr) 235px 260px; gap: 8px; background-color: #050913; background-image: url('/arenas/space-invaders-arena.webp'); }
.space-layout::before { background: linear-gradient(90deg, rgba(2, 8, 18, 0.78), rgba(1, 8, 20, 0.18) 35% 64%, rgba(2, 7, 16, 0.8)), radial-gradient(circle at 50% 48%, transparent 0 34%, rgba(1, 5, 13, 0.3) 68%, rgba(1, 4, 10, 0.62)); }

.arena-panel { position: relative; overflow: hidden; border: 1px solid rgba(225, 235, 255, 0.34); background: linear-gradient(160deg, rgba(13, 18, 29, 0.92), rgba(5, 8, 15, 0.9)); box-shadow: 0 18px 48px rgba(0, 0, 0, 0.5), inset 0 0 34px rgba(93, 116, 163, 0.05); backdrop-filter: blur(13px); border-radius: 4px; }
.mode-killer .arena-panel { border: 0; background: linear-gradient(165deg, rgba(34, 13, 51, 0.94), rgba(13, 7, 25, 0.94) 48%, rgba(7, 5, 14, 0.96)); box-shadow: inset 0 0 0 1px rgba(201, 111, 255, 0.2), inset 0 1px rgba(255, 255, 255, 0.08), 0 18px 45px rgba(0, 0, 0, 0.5); clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 9px, 100% 100%, 9px 100%, 0 calc(100% - 9px)); }
.mode-space .arena-panel { border: 0; background: linear-gradient(160deg, rgba(14, 35, 58, 0.94), rgba(8, 18, 36, 0.94) 50%, rgba(4, 10, 21, 0.97)); box-shadow: inset 0 0 0 1px rgba(89, 210, 255, 0.2), inset 0 1px rgba(255, 255, 255, 0.07), 0 18px 44px rgba(0, 0, 0, 0.52); clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 9px, 100% 100%, 9px 100%, 0 calc(100% - 9px)); }

.arena-ribbon { min-height: 27px; padding: 6px 12px; position: absolute; top: 0; right: 0; left: 0; z-index: 4; display: grid; place-items: center; border-bottom: 1px solid rgba(0, 0, 0, 0.4); background: linear-gradient(100deg, #ffffff, #dcd9e7 58%, #a8a4b1); color: #131723; font-size: 11px; font-weight: 950; letter-spacing: 0.15em; text-align: center; }
.mode-space .arena-ribbon { background: linear-gradient(90deg, #e7fbff, #9defff 65%, #45b9d4); color: #04121e; }

.player-photo { width: 48px; height: 48px; object-fit: cover; border: 3px solid white; border-radius: 50%; background: #152030; }
.large-photo { width: 102px; height: 102px; margin: 12px 0 8px; border-width: 5px; }

.stage-board { width: min(98%, 74vh, 780px); }
.stage-board .board-face { filter: drop-shadow(0 22px 26px rgba(0, 0, 0, 0.64)); }

/* ------------------------------------------------ Donkey Derby */
/* ------------------------------------------------------------ MR vs MRS */
.chore-live-layout {
  min-height: 650px;
  padding: 10px;
  position: relative;
  display: grid;
  grid-template-columns: minmax(640px, 1fr) 272px;
  gap: 10px;
  background:
    radial-gradient(circle at 20% 0%, rgba(56, 189, 248, 0.16), transparent 42%),
    radial-gradient(circle at 82% 4%, rgba(255, 61, 139, 0.16), transparent 42%),
    linear-gradient(150deg, #0d1730, #070c1a 60%, #150a22);
}

.chore-console { min-height: 0; padding: 12px; position: relative; overflow: auto; display: flex; flex-direction: column; gap: 10px; border: 1px solid rgba(120, 160, 220, 0.35); border-radius: 15px; background: linear-gradient(160deg, rgba(17, 27, 50, 0.97), rgba(9, 14, 28, 0.98)); box-shadow: 0 20px 46px rgba(0, 0, 0, 0.45); }
.chore-console-title { min-height: 32px; padding-bottom: 8px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(120, 160, 220, 0.3); }
.chore-console-title span { color: #cfe4ff; font-size: 10px; font-weight: 950; letter-spacing: 0.16em; }
.chore-console-title b { padding: 5px 8px; border: 1px solid rgba(120, 160, 220, 0.5); border-radius: 99px; color: #cfe4ff; font-size: 7px; letter-spacing: 0.12em; }
.chore-console-title b.live { border-color: #38d9f1; background: rgba(56, 217, 241, 0.14); color: #8ceafb; }

.chore-up-now { padding: 10px; position: relative; border: 1px solid rgba(120, 160, 220, 0.32); border-radius: 11px; background: linear-gradient(125deg, rgba(56, 189, 248, 0.12), rgba(255, 61, 139, 0.1)); }
.chore-eyebrow { padding: 4px 8px; position: absolute; top: 0; right: 0; border-radius: 0 9px 0 8px; background: #38d9f1; color: #061421; font-size: 7px; font-weight: 1000; letter-spacing: 0.13em; }
.chore-rider { padding-right: 62px; display: flex; align-items: center; gap: 9px; }
.chore-rider img { width: 45px; height: 45px; border: 3px solid #dbe9ff; border-radius: 50%; object-fit: cover; background: #1a2438; }
.chore-rider > div { min-width: 0; display: flex; flex-direction: column; }
.chore-rider small { color: #7f95bb; font-size: 7px; font-weight: 900; letter-spacing: 0.12em; }
.chore-rider strong { overflow: hidden; font-size: 16px; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.chore-objective { margin: 9px 0 0; color: #cbd8ef; font-size: 11px; line-height: 1.4; }

.chore-section-head { display: flex; align-items: baseline; justify-content: space-between; }
.chore-section-head span { color: #cfe4ff; font-size: 9px; font-weight: 950; letter-spacing: 0.14em; }
.chore-section-head small { color: #7f95bb; font-size: 8px; font-weight: 800; }
.chore-darts { margin-top: 6px; display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.chore-darts > div { padding: 7px 6px; display: flex; flex-direction: column; align-items: center; border: 1px dashed rgba(120, 160, 220, 0.4); border-radius: 9px; }
.chore-darts > div.scored { border-style: solid; border-color: #38d9f1; background: rgba(56, 217, 241, 0.1); }
.chore-darts strong { font-size: 15px; }
.chore-darts span { color: #7f95bb; font-size: 8px; font-weight: 800; letter-spacing: 0.08em; }
.chore-actions { margin-top: auto; }

.derby-live-layout {
  min-height: 650px;
  padding: 10px;
  position: relative;
  display: grid;
  grid-template-columns: minmax(610px, 1fr) 292px;
  gap: 10px;
  background:
    radial-gradient(circle at 25% 0%, rgba(255, 198, 77, 0.16), transparent 35%),
    linear-gradient(135deg, #1b301c, #08160e 58%, #2a110c);
}

.derby-console {
  min-height: 0;
  padding: 12px;
  position: relative;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  border: 2px solid #e6c359;
  border-radius: 15px;
  background:
    linear-gradient(145deg, rgba(72, 20, 15, 0.97), rgba(32, 12, 9, 0.98) 52%, rgba(18, 10, 8, 0.99));
  box-shadow: inset 0 0 0 3px rgba(255, 228, 137, 0.08), 0 20px 46px rgba(0, 0, 0, 0.45);
}

.derby-console-title { min-height: 32px; padding-bottom: 8px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(255, 223, 118, 0.3); }
.derby-console-title span { color: #ffe27a; font-size: 10px; font-weight: 950; letter-spacing: 0.16em; }
.derby-console-title b { padding: 5px 8px; border: 1px solid #d6aa3d; border-radius: 99px; color: #ffe697; font-size: 7px; letter-spacing: 0.12em; }
.derby-console-title b.live { border-color: #6fe887; background: rgba(64, 210, 93, 0.13); color: #9cf6ad; box-shadow: 0 0 12px rgba(81, 229, 108, 0.24); }

.derby-current { padding: 10px; position: relative; border: 1px solid rgba(255, 223, 126, 0.36); border-radius: 11px; background: linear-gradient(125deg, rgba(150, 45, 24, 0.35), rgba(255, 187, 68, 0.08)); }
.derby-up-now { padding: 4px 8px; position: absolute; top: 0; right: 0; border-radius: 0 9px 0 8px; background: #f1c84d; color: #3b1709; font-size: 7px; font-weight: 1000; letter-spacing: 0.13em; }
.derby-rider { padding-right: 53px; display: flex; align-items: center; gap: 9px; }
.derby-rider img { width: 45px; height: 45px; border: 3px solid #fff1bd; border-radius: 50%; object-fit: cover; background: #34241b; box-shadow: 0 0 0 2px #c4882b; }
.derby-rider > div { min-width: 0; display: flex; flex-direction: column; }
.derby-rider small { color: #d9ad62; font-size: 7px; font-weight: 900; letter-spacing: 0.12em; }
.derby-rider strong { overflow: hidden; font-size: 16px; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.derby-aim { margin-top: 9px; display: grid; grid-template-columns: 80px 1fr; align-items: center; gap: 9px; }
.derby-aim > div { width: 74px; height: 74px; display: flex; flex-direction: column; align-items: center; justify-content: center; border: 4px double #5b3618; border-radius: 50%; background: radial-gradient(circle, #fff7d6, #e4bf68); color: #2a180c; box-shadow: 0 0 0 2px #f4d364, 0 8px 20px rgba(0, 0, 0, 0.35); }
.derby-aim small { font-size: 6px; font-weight: 950; letter-spacing: 0.08em; }
.derby-aim strong { font-size: 34px; line-height: 0.9; }
.derby-aim p { margin: 0; color: #f3dcc1; font-size: 12px; line-height: 1.35; }
.derby-aim p b { color: #86ed91; }
.derby-objective { margin-top: 8px; padding: 7px 9px; border-left: 3px solid #ff6f61; background: rgba(255, 100, 76, 0.09); color: #ffd7c8; font-size: 9px; line-height: 1.4; }

.derby-stable, .derby-visit { padding: 9px; border: 1px solid rgba(255, 225, 140, 0.22); border-radius: 10px; background: rgba(12, 9, 7, 0.36); }
.derby-section-head { margin-bottom: 7px; display: flex; align-items: center; justify-content: space-between; }
.derby-section-head span { color: #f4d35e; font-size: 8px; font-weight: 950; letter-spacing: 0.14em; }
.derby-section-head small { color: #aa9781; font-size: 6px; font-weight: 900; letter-spacing: 0.1em; }
.derby-target-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; }
.derby-target-grid article { min-width: 0; min-height: 40px; padding: 4px 5px; display: grid; grid-template-columns: 27px minmax(0, 1fr) 28px; align-items: center; gap: 5px; border: 1px solid color-mix(in srgb, var(--player-accent) 36%, transparent); border-left: 3px solid var(--player-accent); border-radius: 6px; background: rgba(255, 255, 255, 0.04); }
.derby-target-grid article.current { background: color-mix(in srgb, var(--player-accent) 18%, rgba(30, 13, 8, 0.94)); box-shadow: inset 0 0 0 1px var(--player-accent); }
.derby-target-grid article.winner { border-color: #ffdf59; background: rgba(255, 211, 65, 0.16); }
.derby-target-grid img { width: 27px; height: 27px; border: 1px solid #fff; border-radius: 50%; object-fit: cover; }
.derby-target-grid article > div { min-width: 0; display: flex; flex-direction: column; }
.derby-target-grid strong { overflow: hidden; font-size: 8px; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.derby-target-grid small { color: #baa995; font-size: 7px; }
.derby-target-grid article > b { width: 27px; height: 27px; display: grid; place-items: center; border: 2px solid var(--player-accent); border-radius: 50%; background: #f8e9c5; color: #27180e; font-size: 12px; }

.derby-darts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; }
.derby-darts > div { min-width: 0; min-height: 50px; display: flex; flex-direction: column; align-items: center; justify-content: center; border: 1px dashed rgba(255, 232, 168, 0.25); border-radius: 7px; color: #786c5d; }
.derby-darts > div.scored { border-style: solid; border-color: rgba(244, 211, 94, 0.5); background: rgba(244, 211, 94, 0.08); color: #ffe788; }
.derby-darts strong { font-size: 14px; }
.derby-darts span { width: 100%; overflow: hidden; color: #ad9c87; font-size: 6px; font-weight: 900; letter-spacing: 0.04em; text-align: center; text-overflow: ellipsis; white-space: nowrap; }

.derby-message { min-height: 38px; padding: 8px 10px; display: grid; place-items: center; border: 1px solid rgba(249, 207, 80, 0.3); border-radius: 8px; background: linear-gradient(90deg, rgba(125, 39, 21, 0.35), rgba(224, 146, 34, 0.13)); color: #ffe8a5; font-size: 10px; font-weight: 800; line-height: 1.35; text-align: center; }
.derby-actions { margin-top: auto; }
.derby-actions.action-grid .abtn { min-height: 35px; font-size: 9px; }
.derby-actions.action-grid .abtn.takeout { min-height: 40px; }
.derby-actions .round-menu { min-height: 28px; border-color: rgba(244, 211, 94, 0.4); background: rgba(171, 95, 24, 0.18); color: #ffe277; font-size: 9px; }

/* ------------------------------------------------------------ SNAKES & LADDERS */
.snl-live-layout {
  min-height: 650px;
  padding: 10px;
  position: relative;
  display: grid;
  grid-template-columns: minmax(540px, 1fr) 300px;
  gap: 10px;
  background:
    radial-gradient(circle at 24% 0%, rgba(90, 169, 255, 0.16), transparent 42%),
    radial-gradient(circle at 82% 6%, rgba(224, 85, 79, 0.14), transparent 44%),
    linear-gradient(150deg, #10233a, #0a1526 60%, #10233a);
}

.snl-stage { min-width: 0; display: grid; place-items: center; }

.snl-console { min-height: 0; padding: 12px; position: relative; overflow: auto; display: flex; flex-direction: column; gap: 10px; border: 1px solid rgba(120, 160, 220, 0.35); border-radius: 15px; background: linear-gradient(160deg, rgba(17, 27, 50, 0.97), rgba(9, 14, 28, 0.98)); box-shadow: 0 20px 46px rgba(0, 0, 0, 0.45); }
.snl-console-title { min-height: 32px; padding-bottom: 8px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(120, 160, 220, 0.3); }
.snl-console-title span { color: #cfe4ff; font-size: 10px; font-weight: 950; letter-spacing: 0.16em; }
.snl-console-title b { padding: 5px 8px; border: 1px solid rgba(120, 160, 220, 0.5); border-radius: 99px; color: #cfe4ff; font-size: 7px; letter-spacing: 0.12em; }
.snl-console-title b.live { border-color: #38d9f1; background: rgba(56, 217, 241, 0.14); color: #8ceafb; }

.snl-current { padding: 10px; position: relative; border: 1px solid rgba(120, 160, 220, 0.32); border-radius: 11px; background: linear-gradient(125deg, rgba(90, 169, 255, 0.14), rgba(224, 85, 79, 0.08)); }
.snl-up-now { padding: 4px 8px; position: absolute; top: 0; right: 0; border-radius: 0 9px 0 8px; background: #38d9f1; color: #061421; font-size: 7px; font-weight: 1000; letter-spacing: 0.13em; }
.snl-rider { padding-right: 62px; display: flex; align-items: center; gap: 9px; }
.snl-rider img { width: 45px; height: 45px; border: 3px solid #dbe9ff; border-radius: 50%; object-fit: cover; background: #1a2438; }
.snl-rider > div { min-width: 0; display: flex; flex-direction: column; }
.snl-rider small { color: #7f95bb; font-size: 7px; font-weight: 900; letter-spacing: 0.12em; }
.snl-rider strong { font-size: 26px; line-height: 1; }
.snl-aim { margin-top: 9px; display: grid; grid-template-columns: 74px 1fr; align-items: center; gap: 9px; }
.snl-aim > div { width: 68px; height: 68px; display: flex; flex-direction: column; align-items: center; justify-content: center; border: 3px solid #2a4a72; border-radius: 14px; background: radial-gradient(circle, #eaf3ff, #9fc4f4); color: #0d2038; box-shadow: 0 6px 16px rgba(0, 0, 0, 0.35); }
.snl-aim small { font-size: 7px; font-weight: 950; letter-spacing: 0.08em; }
.snl-aim strong { font-size: 30px; line-height: 0.9; }
.snl-aim p { margin: 0; color: #cbd8ef; font-size: 11px; line-height: 1.4; }
.snl-aim p b { color: #8ceafb; }
.snl-objective { margin-top: 8px; padding: 7px 9px; border-left: 3px solid #e0554f; background: rgba(224, 85, 79, 0.1); color: #ffd7c8; font-size: 9px; line-height: 1.4; }

.snl-field, .snl-visit { padding: 9px; border: 1px solid rgba(120, 160, 220, 0.22); border-radius: 10px; background: rgba(10, 16, 30, 0.4); }
.snl-section-head { margin-bottom: 7px; display: flex; align-items: center; justify-content: space-between; }
.snl-section-head span { color: #cfe4ff; font-size: 8px; font-weight: 950; letter-spacing: 0.14em; }
.snl-section-head small { color: #7f95bb; font-size: 6px; font-weight: 900; letter-spacing: 0.1em; }
.snl-target-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; }
.snl-target-grid article { min-width: 0; min-height: 40px; padding: 4px 5px; display: grid; grid-template-columns: 27px minmax(0, 1fr) 30px; align-items: center; gap: 5px; border: 1px solid color-mix(in srgb, var(--player-accent) 36%, transparent); border-left: 3px solid var(--player-accent); border-radius: 6px; background: rgba(255, 255, 255, 0.04); }
.snl-target-grid article.current { background: color-mix(in srgb, var(--player-accent) 18%, rgba(12, 20, 38, 0.94)); box-shadow: inset 0 0 0 1px var(--player-accent); }
.snl-target-grid article.winner { border-color: #ffd54a; background: rgba(255, 213, 74, 0.16); }
.snl-target-grid img { width: 27px; height: 27px; border: 1px solid #fff; border-radius: 50%; object-fit: cover; }
.snl-target-grid article > div { min-width: 0; display: flex; flex-direction: column; }
.snl-target-grid strong { overflow: hidden; font-size: 8px; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.snl-target-grid small { color: #8fa3c4; font-size: 7px; }
.snl-target-grid article > b { width: 30px; height: 27px; display: grid; place-items: center; border: 2px solid var(--player-accent); border-radius: 7px; background: #eaf3ff; color: #10233a; font-size: 13px; }

.snl-darts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; }
.snl-darts > div { min-width: 0; min-height: 48px; display: flex; flex-direction: column; align-items: center; justify-content: center; border: 1px dashed rgba(120, 160, 220, 0.3); border-radius: 7px; color: #6f85a8; }
.snl-darts > div.scored { border-style: solid; border-color: #38d9f1; background: rgba(56, 217, 241, 0.1); color: #cfe4ff; }
.snl-darts strong { font-size: 14px; }
.snl-darts span { color: #7f95bb; font-size: 7px; font-weight: 900; letter-spacing: 0.04em; }

.snl-message { min-height: 38px; padding: 8px 10px; display: grid; place-items: center; border: 1px solid rgba(90, 169, 255, 0.3); border-radius: 8px; background: linear-gradient(90deg, rgba(21, 58, 110, 0.35), rgba(56, 217, 241, 0.1)); color: #dcecff; font-size: 10px; font-weight: 800; line-height: 1.35; text-align: center; }
.snl-actions { margin-top: auto; }
.snl-actions.action-grid .abtn { min-height: 35px; font-size: 9px; }
.snl-actions.action-grid .abtn.takeout { min-height: 40px; }
.snl-actions .round-menu { min-height: 28px; border-color: rgba(90, 169, 255, 0.4); background: rgba(30, 64, 120, 0.3); color: #bcd6ff; font-size: 9px; }

/* ------------------------------------------------ X01 */
.x01-current { padding: 40px 16px 18px; display: flex; flex-direction: column; align-items: center; }
.x01-score-block { width: 100%; padding: 12px 8px 14px; display: flex; flex-direction: column; align-items: center; border: 1px solid rgba(183, 255, 51, 0.36); background: linear-gradient(130deg, rgba(166, 245, 49, 0.96), rgba(83, 196, 33, 0.94)); color: #0d1608; box-shadow: 0 10px 30px rgba(114, 232, 43, 0.2); }
.x01-score-block small { font-size: 9px; font-weight: 950; letter-spacing: 0.15em; opacity: 0.65; }
.x01-score-block strong { font-size: 56px; line-height: 0.98; letter-spacing: -0.05em; text-shadow: 0 1px rgba(255, 255, 255, 0.3); }
.x01-current-darts { width: 100%; min-height: 90px; padding: 15px 7px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; align-items: center; background: rgba(0, 0, 0, 0.45); }
.x01-current-darts span { aspect-ratio: 1; display: grid; place-items: center; border: 2px solid #7d8798; border-radius: 50%; color: #727b88; font-size: 10px; font-weight: 950; }
.x01-current-darts span.filled { border-color: #f1f4f8; background: radial-gradient(circle at 35% 30%, #ffffff, #8390a1 70%); color: #111722; box-shadow: 0 0 14px rgba(255, 255, 255, 0.25); }
.x01-identity { margin-top: auto; padding-top: 14px; display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; }
.x01-photo { width: 104px; height: 104px; border-width: 5px; box-shadow: 0 0 0 4px #a62ed0, 0 18px 42px rgba(0, 0, 0, 0.55); }
.x01-identity strong { max-width: 175px; overflow: hidden; font-size: 20px; letter-spacing: 0.03em; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.x01-stage { min-width: 0; position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 4px; background: radial-gradient(circle at 50% 50%, rgba(14, 31, 61, 0.38), rgba(3, 7, 15, 0.16) 65%); box-shadow: inset 0 0 70px rgba(0, 0, 0, 0.26); }
.board-ribbon { right: 29%; left: 29%; border-radius: 0 0 4px 4px; }
.x01-last-hit { margin-top: -13px; padding: 7px 17px; z-index: 2; display: flex; align-items: center; gap: 9px; border: 1px solid rgba(255, 255, 255, 0.18); border-radius: 99px; background: rgba(5, 9, 17, 0.88); box-shadow: 0 7px 22px rgba(0, 0, 0, 0.45); }
.x01-last-hit small { color: #93a0b2; font-size: 8px; font-weight: 900; letter-spacing: 0.1em; }
.x01-last-hit strong { color: #f7e14e; font-size: 14px; }
.x01-player-list { min-height: 0; flex: 1 1 auto; display: flex; flex-direction: column; gap: 7px; overflow: auto; }
.x01-player-row { min-height: 72px; padding: 7px 9px; display: grid; grid-template-columns: 29px 42px 1fr auto; gap: 8px; align-items: center; border: 1px solid rgba(255, 255, 255, 0.13); border-left: 5px solid var(--player-accent); background: linear-gradient(90deg, color-mix(in srgb, var(--player-accent) 16%, rgba(9, 13, 22, 0.93)), rgba(7, 10, 17, 0.92)); box-shadow: 0 8px 19px rgba(0, 0, 0, 0.27); }
.x01-player-row.current { border-color: var(--player-accent); box-shadow: 0 0 0 1px var(--player-accent), 0 0 20px color-mix(in srgb, var(--player-accent) 25%, transparent); }
.x01-player-row.winner { border-color: #57dc8b; }
.x01-index { width: 27px; height: 27px; display: grid; place-items: center; border-radius: 4px; background: var(--player-accent); color: #10131a; font-size: 11px; font-weight: 950; }
.x01-player-row .player-photo { width: 40px; height: 40px; border-width: 2px; }
.x01-player-row > div { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.x01-player-row small { color: var(--player-accent); font-size: 9px; font-weight: 950; letter-spacing: 0.09em; }
.x01-player-row strong { overflow: hidden; font-size: 13px; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.x01-player-row > b { color: white; font-size: 26px; }

/* ------------------------------------------------ shared panels */
.side-panel { min-height: 0; padding: 39px 9px 10px; display: flex; flex-direction: column; }
.arena-rules { min-height: 0; padding: 32px 22px 16px; display: flex; flex-direction: column; }
.arena-rules-content { min-height: 0; overflow: auto; }
.arena-rules p { margin: 0; padding: 10px 0 10px 16px; position: relative; border-bottom: 1px solid rgba(255, 255, 255, 0.09); color: #d8dfeb; font-size: 12px; line-height: 1.55; }
.arena-rules p::before { position: absolute; top: 1.2em; left: 0; width: 5px; height: 5px; border-radius: 50%; content: ''; background: var(--arena-cyan); box-shadow: 0 0 8px var(--arena-cyan); }
.arena-rules p strong { color: #fff; }

.x01-logo { margin-bottom: 20px; padding-bottom: 16px; display: flex; flex-direction: column; align-items: center; border-bottom: 1px solid rgba(255, 255, 255, 0.16); text-align: center; }
.x01-logo span { font-size: 20px; font-weight: 950; letter-spacing: 0.08em; }
.x01-logo strong { margin: 1px 0; font-size: 64px; font-weight: 950; font-style: italic; line-height: 1; letter-spacing: -0.08em; text-shadow: 0 4px 0 #1f2940; }
.x01-logo small { color: #bbc6d7; font-size: 10px; letter-spacing: 0.12em; }

.embedded-turn { flex: 0 0 auto; margin-top: 10px; padding-top: 10px; border-top: 1px solid rgba(126, 151, 188, 0.25); }
.turn-heading { display: flex; justify-content: space-between; align-items: start; gap: 7px; }
.turn-heading > div { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.turn-heading small { color: #93a0b2; font-size: 9px; letter-spacing: 0.12em; }
.turn-heading strong { max-width: 190px; overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.game-phase { flex: 0 0 auto; border: 1px solid rgba(87, 220, 139, 0.4); border-radius: 99px; padding: 5px 8px; color: var(--arena-green); font-size: 8px; font-weight: 900; letter-spacing: 0.08em; }
.game-phase.warn { color: var(--arena-amber); border-color: rgba(255, 191, 77, 0.55); background: rgba(255, 191, 77, 0.1); }
.game-darts { margin: 8px 0 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; }
.game-dart { min-width: 0; min-height: 58px; padding: 7px 5px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; border: 1px solid rgba(98, 121, 155, 0.28); border-radius: 9px; background: linear-gradient(145deg, rgba(14, 21, 31, 0.94), rgba(7, 11, 17, 0.96)); text-align: center; }
.game-dart:not(.empty) { border-top-color: var(--arena-cyan); }
.game-dart strong { font-size: 15px; }
.game-dart span { width: 100%; overflow: hidden; color: #93a0b2; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.game-dart.empty { color: #536071; font-size: 13px; }

.embedded-controls { margin-top: auto; padding-top: 13px; border-top: 1px solid rgba(126, 151, 188, 0.25); }
.game-message { min-height: 30px; margin: 0 0 9px; display: flex; align-items: center; color: #dce5f1; font-size: 10px; line-height: 1.35; }
.action-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
.action-grid .abtn { width: 100%; min-height: 40px; margin: 0; padding: 7px 6px; border-radius: 8px; font-size: 10px; }
/* The turn-advance is the one button pressed every single turn, and with five
   actions in a two-column grid it would otherwise sit in a half-width cell
   next to a hole. Full width also keeps it the obvious primary. */
.action-grid .abtn.takeout { grid-column: 1 / -1; min-height: 46px; font-size: 11px; }
.round-menu { grid-column: 1 / -1; min-height: 31px; border: 1px solid rgba(56, 217, 241, 0.35); border-radius: 8px; background: rgba(22, 139, 216, 0.22); color: var(--arena-cyan); font-size: 14px; font-weight: 900; cursor: pointer; }

/* ------------------------------------------------ Killer */
.mode-killer .killer-current { padding: 42px 14px 15px; display: flex; flex-direction: column; align-items: center; text-align: center; background: radial-gradient(circle at 50% 19%, rgba(143, 48, 200, 0.64), transparent 33%), linear-gradient(180deg, rgba(38, 13, 57, 0.92), rgba(8, 5, 15, 0.97)); }
.mode-killer .killer-current .large-photo { border: 5px solid #f7ecff; outline: 3px solid #ca38ff; box-shadow: 0 0 0 7px rgba(112, 21, 150, 0.42), 0 0 32px rgba(231, 54, 255, 0.62), 0 19px 38px rgba(0, 0, 0, 0.7); animation: killer-current-glow 2.8s ease-in-out infinite; }
.killer-name { margin-top: 3px; max-width: 180px; overflow: hidden; color: #f7f4ff; font-family: Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif; font-size: 22px; font-weight: 900; letter-spacing: 0.035em; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; text-shadow: 0 2px 0 #52126d, 0 0 14px rgba(230, 79, 255, 0.34); }
.killer-assignment { width: 100%; margin-top: 10px; display: flex; flex-direction: column; }
.killer-assignment b { min-height: 42px; display: grid; place-items: center; color: #baff19; font-family: Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif; font-size: clamp(22px, 2vw, 34px); letter-spacing: 0.015em; line-height: 1; text-shadow: 0 3px 0 #355900, 0 0 10px rgba(186, 255, 25, 0.82), 0 0 25px rgba(186, 255, 25, 0.38); }
.killer-assignment span { margin-top: 4px; color: #cfc3d9; font-size: 8px; font-weight: 900; letter-spacing: 0.13em; }
.killer-objective { width: 100%; min-height: 100px; margin-top: 13px; padding: 12px 10px; display: flex; flex-direction: column; justify-content: center; gap: 6px; border: 0; border-left: 4px solid #baff19; border-radius: 4px 9px 9px 4px; background: radial-gradient(circle at 0 50%, rgba(186, 255, 25, 0.17), transparent 56%), linear-gradient(145deg, rgba(45, 20, 62, 0.96), rgba(19, 11, 30, 0.96)); box-shadow: inset 0 1px rgba(255, 255, 255, 0.07), 0 12px 28px rgba(0, 0, 0, 0.34), 0 0 18px rgba(186, 255, 25, 0.08); text-align: left; }
.killer-objective small { color: #baff19; font-size: 9px; font-weight: 950; letter-spacing: 0.15em; }
.killer-objective strong { color: #f7f8f2; font-size: 14px; line-height: 1.38; }
.killer-marks { margin-top: 12px; display: flex; gap: 8px; }
.killer-marks span { width: 30px; height: 30px; display: grid; place-items: center; border: 2px solid #604670; border-radius: 50%; background: radial-gradient(circle at 38% 30%, #281a32, #0b0810 72%); color: #343c49; box-shadow: inset 0 3px 6px rgba(255, 255, 255, 0.04), 0 5px 10px rgba(0, 0, 0, 0.35); }
.killer-marks span.earned { border-color: #d8ff65; background: radial-gradient(circle at 36% 28%, #e5ff8c, #8fd50f 66%, #436a00); color: #10170c; box-shadow: 0 0 17px rgba(186, 255, 25, 0.6), inset 0 2px rgba(255, 255, 255, 0.55); }
.killer-lives { margin-top: auto; padding-top: 13px; display: flex; gap: 8px; }
.killer-lives span { color: #382c40; font-size: 25px; filter: grayscale(1); }
.killer-lives span.alive { color: #dc47ef; filter: drop-shadow(0 0 8px rgba(220, 71, 239, 0.65)); }

.killer-target { min-width: 0; position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; overflow: hidden; }
.killer-target::before { width: min(96%, 780px); aspect-ratio: 1; position: absolute; z-index: 0; border-radius: 50%; content: ''; background: radial-gradient(circle, rgba(151, 56, 207, 0.23) 0 44%, rgba(182, 51, 255, 0.12) 51%, transparent 69%); box-shadow: 0 0 75px rgba(184, 55, 255, 0.24); animation: killer-board-aura 3.5s ease-in-out infinite; }
.killer-target > * { position: relative; z-index: 1; }
.killer-round { position: absolute; top: 14px; z-index: 3; padding: 6px 17px; border: 1px solid #dcff78; border-radius: 99px; background: linear-gradient(180deg, #dcff6c, #9ee817); color: #152000; font-size: 10px; font-weight: 900; letter-spacing: 0.08em; box-shadow: 0 0 22px rgba(186, 255, 25, 0.48), inset 0 2px rgba(255, 255, 255, 0.65); }
.killer-board .board-face { filter: drop-shadow(0 26px 28px rgba(0, 0, 0, 0.8)) drop-shadow(0 0 17px rgba(185, 64, 255, 0.4)); }
.killer-turn-track { margin-top: -8px; z-index: 2; display: flex; gap: 12px; }
.killer-turn-track span { min-width: 30px; height: 30px; padding: 0 5px; display: grid; place-items: center; border: 2px solid #5d3b69; border-radius: 99px; background: radial-gradient(circle at 35% 28%, #291734, #08050d 70%); color: #513a5b; font-size: 11px; font-weight: 800; box-shadow: inset 0 2px 4px rgba(255, 255, 255, 0.04), 0 6px 12px rgba(0, 0, 0, 0.5); }
.killer-turn-track span.hit { border-color: #ff6cf1; background: radial-gradient(circle at 35% 30%, #ff9bf8, #9924bf 68%); color: white; box-shadow: 0 0 17px rgba(240, 71, 255, 0.62); }

.killer-players { min-height: 0; flex: 1 1 auto; display: flex; flex-direction: column; gap: 6px; overflow: auto; }
.killer-player { min-height: 64px; padding: 7px 8px; display: grid; grid-template-columns: minmax(54px, auto) 38px minmax(0, 1fr) auto; gap: 7px; align-items: center; border: 0; border-left: 5px solid var(--player-accent); border-radius: 4px 9px 9px 4px; background: linear-gradient(100deg, color-mix(in srgb, var(--player-accent) 18%, #170d20), rgba(8, 6, 13, 0.97) 76%); box-shadow: inset 0 1px rgba(255, 255, 255, 0.06), 0 8px 18px rgba(0, 0, 0, 0.35); }
.killer-player.current { background: linear-gradient(100deg, color-mix(in srgb, var(--player-accent) 35%, #21102c), rgba(14, 7, 21, 0.98) 78%); box-shadow: inset 0 0 0 1px var(--player-accent), 0 0 20px color-mix(in srgb, var(--player-accent) 38%, transparent); animation: killer-row-pulse 2.5s ease-in-out infinite; }
.killer-player.eliminated { opacity: 0.36; filter: grayscale(1); }
.killer-player .killer-number { min-width: 54px; height: 38px; padding: 0 6px; display: grid; place-items: center; border: 1px solid color-mix(in srgb, var(--player-accent) 80%, white); border-radius: 6px; background: linear-gradient(145deg, color-mix(in srgb, var(--player-accent) 88%, white), var(--player-accent)); color: #11131a; font-size: 12px; font-weight: 900; line-height: 1; box-shadow: 0 0 12px color-mix(in srgb, var(--player-accent) 34%, transparent), inset 0 1px rgba(255, 255, 255, 0.55); white-space: nowrap; }
.killer-player .player-photo { width: 38px; height: 38px; border-color: #f7f4ff; box-shadow: 0 0 0 2px var(--player-accent), 0 0 12px color-mix(in srgb, var(--player-accent) 40%, transparent); }
.killer-player > div { min-width: 0; display: flex; flex-direction: column; }
.killer-player > div strong { overflow: hidden; color: white; font-size: 12px; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.killer-player > div span { margin-top: 2px; color: #c8bdce; font-size: 9px; }
.killer-player > i { display: flex; gap: 2px; font-style: normal; }
.killer-player em { color: #333b48; font-size: 12px; font-style: normal; }
.killer-player em.alive { color: var(--player-accent); filter: drop-shadow(0 0 4px var(--player-accent)); }

.killer-rules { background: radial-gradient(circle at 50% 0, rgba(103, 19, 134, 0.32), transparent 35%), linear-gradient(170deg, rgba(24, 7, 34, 0.97), rgba(6, 5, 11, 0.98)); }
.killer-logo { height: 102px; margin: -5px 0 9px; padding: 0; overflow: visible; }
.killer-logo svg { width: 100%; height: 102px; display: block; overflow: visible; }
.killer-logo text { font-family: Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif; font-size: 63px; font-style: italic; font-weight: 950; letter-spacing: 2px; text-anchor: middle; }
.killer-logo-shadow { fill: #21032d; stroke: #280035; stroke-width: 11px; transform: translateY(7px); }
.killer-logo-text { fill: url(#killer-title-gradient); stroke: #ffb7ff; stroke-width: 1.6px; paint-order: stroke fill; }
.mode-killer .arena-rules p::before { width: 7px; height: 7px; border-radius: 2px; background: linear-gradient(145deg, #ff79f2, #a424e0); box-shadow: 0 0 9px #ec4cff; transform: rotate(45deg); }

@keyframes killer-current-glow { 50% { box-shadow: 0 0 0 7px rgba(112, 21, 150, 0.5), 0 0 43px rgba(231, 54, 255, 0.78), 0 20px 40px rgba(0, 0, 0, 0.7); } }
@keyframes killer-board-aura { 50% { opacity: 0.7; transform: scale(1.035); } }
@keyframes killer-row-pulse { 50% { filter: brightness(1.09); } }

/* ------------------------------------------------ Space */
.space-current { padding: 40px 12px 14px; display: flex; flex-direction: column; align-items: center; background: radial-gradient(circle at 50% 17%, rgba(54, 157, 211, 0.34), transparent 31%), linear-gradient(180deg, rgba(8, 34, 58, 0.95), rgba(3, 10, 21, 0.97)) !important; }
.space-pilot-frame { width: 96px; height: 96px; margin: 8px 0 7px; position: relative; display: grid; place-items: center; border-radius: 50%; background: conic-gradient(from 12deg, transparent, #45e4ff, transparent 34%, #3a7dff, transparent 68%, #45e4ff); box-shadow: 0 0 32px rgba(69, 228, 255, 0.3); animation: space-pilot-ring 7s linear infinite; }
.space-pilot-frame::before { position: absolute; inset: -6px; border: 1px dashed rgba(157, 244, 255, 0.46); border-radius: inherit; content: ''; }
.space-pilot-frame .large-photo { width: 84px; height: 84px; margin: 0; border: 4px solid #eafcff; box-shadow: 0 0 0 3px #12354d, 0 0 22px rgba(69, 228, 255, 0.52); animation: space-pilot-ring 7s linear infinite reverse; }
.space-pilot-frame i { width: 8px; height: 8px; position: absolute; top: -3px; border-radius: 50%; background: #b8ff36; box-shadow: 0 0 12px #b8ff36; }
.space-name { max-width: 160px; overflow: hidden; color: white; font-size: 20px; letter-spacing: 0.06em; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; text-shadow: 0 0 15px rgba(69, 228, 255, 0.35); }
.space-score-grid { width: 100%; margin-top: 10px; display: grid; grid-template-columns: 0.75fr 1.25fr; gap: 6px; }
.space-score-grid > div { min-height: 66px; padding: 8px 4px 6px; display: flex; flex-direction: column; align-items: center; justify-content: center; background: linear-gradient(145deg, rgba(17, 45, 72, 0.9), rgba(4, 12, 23, 0.94)); box-shadow: inset 0 1px rgba(255, 255, 255, 0.05); text-align: center; }
.space-score-grid small { color: #8fb0c5; font-size: 8px; font-weight: 900; letter-spacing: 0.1em; }
.space-score-grid b { color: #9df4ff; font-size: 30px; line-height: 1; text-shadow: 0 0 12px rgba(69, 228, 255, 0.42); }
.space-reactor { width: 100%; margin-top: 8px; padding: 9px; background: rgba(3, 12, 23, 0.82); box-shadow: inset 0 0 0 1px rgba(69, 228, 255, 0.14); }
.space-reactor > div:first-child { margin-bottom: 7px; display: flex; justify-content: space-between; align-items: center; }
.space-reactor small { color: #8aa4b8; font-size: 8px; font-weight: 900; letter-spacing: 0.1em; }
.space-reactor strong { color: #71879a; font-size: 9px; letter-spacing: 0.08em; }
.space-reactor strong.ready { color: #b8ff36; text-shadow: 0 0 9px rgba(184, 255, 54, 0.6); }
.space-cannon-meter { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }
.space-cannon-meter i { height: 10px; display: block; background: #14263a; box-shadow: inset 0 0 0 1px rgba(157, 244, 255, 0.12); }
.space-cannon-meter i.charged { background: linear-gradient(90deg, #54cfff, #b8ff36); box-shadow: 0 0 10px rgba(184, 255, 54, 0.48); }
.space-cannon-meter.ready { animation: space-reactor-ready 1.8s ease-in-out infinite; }
.space-objective { width: 100%; min-height: 84px; margin-top: 8px; padding: 10px 9px; display: flex; flex-direction: column; justify-content: center; gap: 5px; border-left: 3px solid #45e4ff; background: linear-gradient(105deg, rgba(23, 84, 111, 0.36), rgba(5, 15, 28, 0.86)); text-align: left; }
.space-objective small { color: #45e4ff; font-size: 8px; font-weight: 950; letter-spacing: 0.12em; }
.space-objective strong { color: #edfaff; font-size: 12px; line-height: 1.35; }
.space-defence { width: 100%; margin-top: 8px; padding: 10px 9px; display: flex; flex-direction: column; gap: 10px; background: linear-gradient(155deg, rgba(8, 30, 50, 0.9), rgba(3, 12, 23, 0.92)); box-shadow: inset 0 0 0 1px rgba(69, 228, 255, 0.13); }
.space-defence-heading, .space-telemetry { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
.space-defence-heading > span, .space-telemetry span { display: flex; flex-direction: column; gap: 2px; }
.space-defence small { color: #7592a7; font-size: 7px; font-weight: 900; letter-spacing: 0.09em; }
.space-defence b { color: #9df4ff; font-size: 9px; letter-spacing: 0.06em; }
.space-shields { display: flex; gap: 4px; }
.space-shields i { width: 17px; height: 20px; display: block; clip-path: polygon(50% 0, 92% 18%, 83% 74%, 50% 100%, 17% 74%, 8% 18%); background: #1a3043; }
.space-shields i.active { background: linear-gradient(145deg, #e8ff9f, #b8ff36 50%, #4d8e09); filter: drop-shadow(0 0 6px rgba(184, 255, 54, 0.58)); }
.space-shields i.lost { background: #291923; box-shadow: inset 0 0 0 1px rgba(255, 79, 104, 0.22); }
.space-threat { display: flex; flex-direction: column; gap: 5px; }
.space-threat > span { display: flex; justify-content: space-between; align-items: center; }
.space-threat > div { height: 8px; overflow: hidden; background: #112537; }
.space-threat > div i { height: 100%; display: block; background: linear-gradient(90deg, #3a7dff, #45e4ff, #b8ff36); box-shadow: 0 0 9px rgba(69, 228, 255, 0.38); transition: width 0.45s ease; }
.space-telemetry { margin-top: auto; padding-top: 9px; border-top: 1px solid rgba(69, 228, 255, 0.1); }
.space-telemetry span:last-child { text-align: right; }
.space-missile-bays { width: 100%; margin-top: auto; padding-top: 10px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; }
.space-missile-bays span { min-width: 0; height: 46px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; background: #07101d; box-shadow: inset 0 0 0 1px rgba(69, 228, 255, 0.17); }
.space-missile-bays i { width: 7px; height: 19px; display: block; border-radius: 50% 50% 2px 2px; background: #b8ff36; box-shadow: 0 0 8px rgba(184, 255, 54, 0.5); }
.space-missile-bays b { max-width: 100%; overflow: hidden; color: #a9bed0; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.space-missile-bays .spent i { height: 5px; background: #304154; box-shadow: none; }
.space-missile-bays .spent { opacity: 0.58; }

.space-playfield { min-width: 0; padding: 9px 10px 7px; position: relative; display: flex; flex-direction: column; align-items: center; overflow: hidden; border-radius: 4px; background: radial-gradient(circle at 50% 50%, rgba(25, 98, 148, 0.15), rgba(1, 7, 17, 0.11) 66%); box-shadow: inset 0 0 0 1px rgba(69, 228, 255, 0.15), inset 0 0 80px rgba(0, 0, 0, 0.3); }
.space-strip { width: min(92%, 600px); min-height: 40px; z-index: 5; display: grid; grid-template-columns: repeat(3, 1fr); background: linear-gradient(90deg, rgba(6, 20, 36, 0.9), rgba(13, 42, 66, 0.9), rgba(6, 20, 36, 0.9)); box-shadow: 0 7px 24px rgba(0, 0, 0, 0.4), inset 0 -1px rgba(69, 228, 255, 0.24); }
.space-strip span { padding: 6px 10px; display: flex; align-items: center; justify-content: center; gap: 7px; }
.space-strip span + span { border-left: 1px solid rgba(69, 228, 255, 0.14); }
.space-strip small { color: #7994aa; font-size: 8px; font-weight: 900; letter-spacing: 0.1em; }
.space-strip b { color: #9df4ff; font-size: 12px; }
.space-playfield .space-orbit-stage { width: min(100%, 74vh, 760px); flex: 1 1 auto; }
.space-turn-track { z-index: 5; display: flex; gap: 7px; }
.space-turn-track span { min-width: 58px; min-height: 25px; padding: 5px 8px; display: grid; place-items: center; background: rgba(3, 12, 23, 0.88); color: #577187; font-size: 8px; font-weight: 900; letter-spacing: 0.07em; box-shadow: inset 0 0 0 1px rgba(69, 228, 255, 0.16); }
.space-turn-track span.fired { background: linear-gradient(180deg, rgba(32, 141, 180, 0.72), rgba(8, 38, 62, 0.9)); color: white; box-shadow: inset 0 0 0 1px #45e4ff, 0 0 12px rgba(69, 228, 255, 0.22); }

.space-players { min-height: 0; flex: 1 1 auto; display: flex; flex-direction: column; gap: 5px; overflow: auto; }
.space-player { min-height: 59px; padding: 6px; display: grid; grid-template-columns: 24px 36px minmax(0, 1fr) auto auto; gap: 6px; align-items: center; border-left: 4px solid var(--player-accent); background: linear-gradient(100deg, color-mix(in srgb, var(--player-accent) 13%, #0b1b2d), rgba(4, 11, 21, 0.95)); box-shadow: inset 0 1px rgba(255, 255, 255, 0.045), 0 7px 17px rgba(0, 0, 0, 0.32); }
.space-player.current { background: linear-gradient(100deg, color-mix(in srgb, var(--player-accent) 28%, #102a43), rgba(5, 14, 25, 0.97)); box-shadow: inset 0 0 0 1px #45e4ff, 0 0 17px rgba(69, 228, 255, 0.22); }
.space-rank { width: 23px; height: 23px; display: grid; place-items: center; background: var(--player-accent); color: #07101a; font-size: 9px; font-weight: 950; }
.space-player .player-photo { width: 34px; height: 34px; border-width: 2px; box-shadow: 0 0 0 1px var(--player-accent); }
.space-player > div { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.space-player > div strong { overflow: hidden; color: white; font-size: 11px; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.space-player > div span { color: #7f9bb0; font-size: 8px; font-weight: 900; letter-spacing: 0.07em; }
.space-player > b, .space-player > i { min-width: 30px; display: flex; align-items: baseline; justify-content: flex-end; gap: 2px; color: #9df4ff; font-size: 14px; font-style: normal; }
.space-player > i { color: #b8ff36; }
.space-player small { color: #70899c; font-size: 6px; }
.space-fleet-summary { min-height: 100px; margin-top: 8px; padding: 10px 8px; display: grid; grid-template-columns: 65px minmax(0, 1fr); gap: 9px; align-items: center; background: radial-gradient(circle at 24% 50%, rgba(69, 228, 255, 0.11), transparent 35%), linear-gradient(145deg, rgba(8, 31, 52, 0.92), rgba(3, 12, 23, 0.96)); box-shadow: inset 0 0 0 1px rgba(69, 228, 255, 0.13); }
.space-fleet-gauge { width: 62px; height: 62px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-radius: 50%; background: radial-gradient(circle at center, #081523 0 58%, transparent 59%), conic-gradient(#45e4ff var(--fleet-angle), rgba(69, 228, 255, 0.1) 0); box-shadow: 0 0 17px rgba(69, 228, 255, 0.13); }
.space-fleet-gauge strong { color: white; font-size: 19px; line-height: 1; }
.space-fleet-gauge small { color: #7694a9; font-size: 6px; font-weight: 900; letter-spacing: 0.08em; }
.space-fleet-summary > div:last-child { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.space-fleet-summary > div:last-child small { color: #45e4ff; font-size: 7px; font-weight: 950; letter-spacing: 0.1em; }
.space-fleet-summary > div:last-child b { overflow: hidden; color: #edfaff; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.space-fleet-summary > div:last-child span { color: #7f9aae; font-size: 8px; line-height: 1.35; }

.space-rules { background: radial-gradient(circle at 50% 0, rgba(26, 112, 151, 0.27), transparent 34%), linear-gradient(170deg, rgba(7, 25, 43, 0.98), rgba(3, 9, 19, 0.99)) !important; }
.space-logo { margin-bottom: 10px; padding: 5px 0 13px; position: relative; display: flex; flex-direction: column; align-items: center; overflow: hidden; border-bottom: 1px solid rgba(69, 228, 255, 0.22); text-align: center; }
.space-logo small { color: #45e4ff; font-size: 8px; font-weight: 950; letter-spacing: 0.28em; }
.space-logo strong, .space-logo b { position: relative; z-index: 1; font-family: Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif; font-style: italic; font-weight: 950; letter-spacing: 0.04em; line-height: 0.82; transform: skewX(-8deg); }
.space-logo strong { margin-top: 7px; color: #f5fdff; font-size: 42px; text-shadow: 0 4px 0 #164d75, 0 0 18px rgba(69, 228, 255, 0.45); }
.space-logo b { color: #45e4ff; font-size: 36px; -webkit-text-stroke: 1px #d5fbff; text-shadow: 0 4px 0 #12395a, 0 0 20px rgba(69, 228, 255, 0.52); }
.space-logo i { width: 150px; height: 50px; position: absolute; bottom: 2px; border: 1px solid rgba(69, 228, 255, 0.24); border-radius: 50%; transform: rotate(-7deg); }
.mode-space .arena-rules p::before { width: 6px; height: 6px; border-radius: 1px; background: #45e4ff; box-shadow: 0 0 8px #45e4ff; transform: rotate(45deg); }

@keyframes space-pilot-ring { to { transform: rotate(360deg); } }
@keyframes space-reactor-ready { 50% { filter: brightness(1.35); transform: scaleX(0.98); } }

/* ------------------------------------------------ darts golf
   The other three arenas sit on a painted background image. There is no golf
   one, so the course is built from gradients - a dusk sky over a fairway that
   darkens towards the rough at the edges. Self-contained, and it will never
   look like a missing asset. */
.golf-layout { grid-template-columns: 215px minmax(380px, 1fr) 300px 280px; background-color: #0b1c0e; background-image: linear-gradient(180deg, #10283a 0%, #1d4436 32%, #1c4023 55%, #0d2211 100%); }
.golf-layout::before { background: radial-gradient(ellipse at 50% 78%, rgba(93, 168, 96, 0.22) 0 30%, transparent 62%), linear-gradient(90deg, rgba(4, 14, 7, 0.72), rgba(4, 14, 7, 0.12) 46% 58%, rgba(4, 14, 7, 0.74)); }
.mode-golf .arena-panel { border: 0; background: linear-gradient(160deg, rgba(20, 46, 26, 0.94), rgba(10, 26, 14, 0.95) 52%, rgba(5, 15, 8, 0.97)); box-shadow: inset 0 0 0 1px rgba(127, 191, 90, 0.22), inset 0 1px rgba(255, 255, 255, 0.06), 0 18px 44px rgba(0, 0, 0, 0.5); border-radius: 4px; }
.mode-golf .arena-ribbon { background: linear-gradient(90deg, #fdf3d6, #f2c14e 62%, #c79a2f); color: #22300f; }

.mode-golf .golf-current { padding: 40px 13px 13px; display: flex; flex-direction: column; align-items: center; gap: 10px; text-align: center; }
.golf-flagbox { width: 100%; position: relative; }
.golf-flagbox svg { display: block; width: 100%; height: auto; }
.golf-flagnum { position: absolute; top: 4px; left: 6px; display: flex; flex-direction: column; align-items: flex-start; line-height: 1; }
.golf-flagnum small { color: #9fc79b; font-size: 8px; font-weight: 900; letter-spacing: 0.16em; }
.golf-flagnum strong { color: #f2c14e; font-size: 38px; font-weight: 950; text-shadow: 0 3px 12px rgba(0, 0, 0, 0.6); }
.golf-flagnum em { color: #cfe3c9; font-size: 9px; font-style: normal; font-weight: 800; letter-spacing: 0.1em; }

.golf-identity { display: flex; flex-direction: column; align-items: center; gap: 5px; }
.golf-identity strong { color: #f4f7ef; font-size: 15px; font-weight: 900; letter-spacing: 0.05em; text-transform: uppercase; }
.golf-photo { width: 76px; height: 76px; border: 3px solid #f2c14e; box-shadow: 0 0 0 4px rgba(47, 122, 60, 0.5), 0 12px 26px rgba(0, 0, 0, 0.6); }

.golf-strokes { width: 100%; padding: 7px 9px; display: flex; flex-direction: column; align-items: center; gap: 1px; border-radius: 4px; background: rgba(6, 20, 9, 0.72); box-shadow: inset 0 0 0 1px rgba(127, 191, 90, 0.2); }
.golf-strokes small { color: #86a882; font-size: 8px; font-weight: 900; letter-spacing: 0.16em; }
.golf-strokes b { color: #f6f2e2; font-family: ui-monospace, monospace; font-size: 34px; line-height: 1; }
.golf-strokes em { color: #f2c14e; font-size: 10px; font-style: normal; font-weight: 800; letter-spacing: 0.08em; }

.golf-balls { width: 100%; display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; }
.golf-balls span { padding: 5px 2px; display: flex; flex-direction: column; align-items: center; gap: 3px; border-radius: 4px; background: rgba(6, 20, 9, 0.6); box-shadow: inset 0 0 0 1px rgba(127, 191, 90, 0.16); }
.golf-balls i { width: 13px; height: 13px; border-radius: 50%; background: radial-gradient(circle at 35% 32%, #ffffff, #b9bcae); box-shadow: 0 1px 3px rgba(0, 0, 0, 0.5); }
.golf-balls span.played i { background: radial-gradient(circle at 35% 32%, #f7dd9a, #c79a2f); }
.golf-balls b { color: #cfe3c9; font-family: ui-monospace, monospace; font-size: 10px; }
.golf-balls span.played b { color: #f2c14e; }

.golf-payout { width: 100%; display: grid; gap: 3px; }
.golf-payout span { display: flex; align-items: center; gap: 6px; padding: 3px 6px; border-radius: 3px; background: rgba(6, 20, 9, 0.55); color: #a8c4a3; font-size: 9px; font-weight: 800; letter-spacing: 0.08em; }
.golf-payout i { width: 8px; height: 8px; border-radius: 2px; }
.golf-payout b { margin-left: auto; color: #f4f7ef; font-family: ui-monospace, monospace; font-size: 12px; }
.golf-payout i.ace { background: #f2c14e; }
.golf-payout i.birdie { background: #57c26b; }
.golf-payout i.par { background: #2f5f36; }
.golf-payout i.bogey { background: #a3423a; }

.golf-green { min-width: 0; padding: 9px 10px 7px; position: relative; display: flex; flex-direction: column; align-items: center; overflow: hidden; border-radius: 4px; background: radial-gradient(circle at 50% 52%, rgba(79, 154, 74, 0.2), rgba(4, 14, 7, 0.12) 66%); box-shadow: inset 0 0 0 1px rgba(127, 191, 90, 0.16), inset 0 0 80px rgba(0, 0, 0, 0.32); }
.golf-strip { width: 100%; display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.golf-strip span { padding: 4px 8px; display: flex; flex-direction: column; border-radius: 3px; background: rgba(6, 20, 9, 0.7); }
.golf-strip small { color: #86a882; font-size: 8px; font-weight: 900; letter-spacing: 0.14em; }
.golf-strip b { overflow: hidden; color: #f4f7ef; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.golf-board { width: min(100%, 66vh, 680px); flex: 1 1 auto; display: grid; place-items: center; }
.golf-lastshot { margin-top: 2px; padding: 6px 16px; display: flex; align-items: center; gap: 9px; border-radius: 99px; background: rgba(5, 16, 8, 0.9); box-shadow: inset 0 0 0 1px rgba(242, 193, 78, 0.3); }
.golf-lastshot small { color: #86a882; font-size: 8px; font-weight: 900; letter-spacing: 0.1em; }
.golf-lastshot strong { color: #f2c14e; font-family: ui-monospace, monospace; font-size: 14px; }

.mode-golf .golf-side { padding: 39px 10px 10px; }
.golf-logo { margin-bottom: 10px; padding: 12px 10px; display: flex; flex-direction: column; align-items: center; gap: 2px; border-radius: 4px; background: linear-gradient(180deg, rgba(47, 122, 60, 0.35), rgba(6, 20, 9, 0.6)); box-shadow: inset 0 0 0 1px rgba(242, 193, 78, 0.25); }
.golf-logo span { color: #9fc79b; font-size: 9px; font-weight: 900; letter-spacing: 0.24em; }
.golf-logo strong { color: #f2c14e; font-family: Georgia, 'Times New Roman', serif; font-size: 40px; font-weight: 700; letter-spacing: 0.1em; line-height: 1; text-shadow: 0 3px 10px rgba(0, 0, 0, 0.55); }
.golf-logo small { color: #cfe3c9; font-size: 9px; font-weight: 800; letter-spacing: 0.1em; }
.mode-golf .arena-rules p::before { width: 6px; height: 6px; border-radius: 50%; background: #f2c14e; box-shadow: 0 0 8px rgba(242, 193, 78, 0.8); }

/* ------------------------------------------------ noughts & crosses
   The mockup's deep navy arena, blue X vs red O. Like golf there is no
   painted background asset, so the arena is gradients only. */
.oxo-layout { grid-template-columns: 215px minmax(430px, 1fr) 270px 280px; background-color: #0a0f1e; background-image: radial-gradient(ellipse at 50% 0%, rgba(43, 66, 120, 0.5), transparent 55%), linear-gradient(180deg, #0d1428 0%, #0a0f1e 60%, #070b15 100%); }
.oxo-layout::before { background: linear-gradient(90deg, rgba(4, 7, 15, 0.7), rgba(4, 7, 15, 0.1) 46% 58%, rgba(4, 7, 15, 0.72)); }
.mode-oxo .arena-panel { border: 0; background: linear-gradient(160deg, rgba(19, 28, 52, 0.94), rgba(10, 15, 30, 0.95) 52%, rgba(6, 9, 18, 0.97)); box-shadow: inset 0 0 0 1px rgba(110, 140, 200, 0.24), inset 0 1px rgba(255, 255, 255, 0.06), 0 18px 44px rgba(0, 0, 0, 0.5); border-radius: 4px; }
.mode-oxo .arena-ribbon { background: linear-gradient(90deg, #dce8ff, #8fb8ff 62%, #4b74c9); color: #0a1226; }

/* The two marks own two colours, used identically everywhere they appear. */
.mode-oxo .x, .oxo-logo i.x { --mark: #4ba3ff; }
.mode-oxo .o, .oxo-logo i.o { --mark: #ff5a5a; }

.mode-oxo .oxo-current { padding: 40px 13px 13px; display: flex; flex-direction: column; align-items: center; gap: 10px; text-align: center; }
.oxo-bigmark { width: 84px; height: 84px; display: grid; place-items: center; border-radius: 10px; background: rgba(6, 10, 22, 0.7); box-shadow: inset 0 0 0 2px var(--mark, #4ba3ff); color: var(--mark, #4ba3ff); font-size: 56px; font-weight: 950; line-height: 1; text-shadow: 0 0 22px color-mix(in srgb, var(--mark, #4ba3ff) 75%, transparent); }
.oxo-identity { display: flex; flex-direction: column; align-items: center; gap: 5px; }
.oxo-identity strong { color: #f0f4ff; font-size: 15px; font-weight: 900; letter-spacing: 0.05em; text-transform: uppercase; }
.oxo-photo { width: 68px; height: 68px; border: 3px solid var(--mark, #4ba3ff); box-shadow: 0 0 0 4px color-mix(in srgb, var(--mark, #4ba3ff) 30%, transparent), 0 12px 26px rgba(0, 0, 0, 0.6); }
.oxo-darts { width: 100%; display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; }
.oxo-darts span { padding: 7px 2px; border-radius: 4px; background: rgba(6, 10, 22, 0.6); box-shadow: inset 0 0 0 1px rgba(110, 140, 200, 0.2); color: #6f83ad; font-family: ui-monospace, monospace; font-size: 11px; }
.oxo-darts span.thrown { color: #dce8ff; box-shadow: inset 0 0 0 1px rgba(143, 184, 255, 0.5); }
.oxo-owned { width: 100%; padding: 7px 9px; display: flex; flex-direction: column; align-items: center; gap: 1px; border-radius: 4px; background: rgba(6, 10, 22, 0.72); box-shadow: inset 0 0 0 1px rgba(110, 140, 200, 0.2); }
.oxo-owned small { color: #7d90ba; font-size: 8px; font-weight: 900; letter-spacing: 0.16em; }
.oxo-owned b { color: #f2f6ff; font-family: ui-monospace, monospace; font-size: 30px; line-height: 1; }
.oxo-hintbox { width: 100%; padding: 8px 9px; border-radius: 4px; background: rgba(242, 193, 78, 0.1); box-shadow: inset 0 0 0 1px rgba(242, 193, 78, 0.45); color: #ffd98a; font-size: 11px; font-weight: 800; letter-spacing: 0.04em; }

.oxo-stage { min-width: 0; padding: 12px; position: relative; display: flex; flex-direction: column; overflow: hidden; border-radius: 4px; background: radial-gradient(circle at 50% 52%, rgba(60, 92, 160, 0.16), rgba(4, 7, 15, 0.1) 66%); box-shadow: inset 0 0 0 1px rgba(110, 140, 200, 0.16), inset 0 0 80px rgba(0, 0, 0, 0.32); }
.oxo-play { min-height: 0; flex: 1 1 auto; display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); gap: 12px; align-items: center; }
.oxo-grid { width: min(100%, 64vh); aspect-ratio: 1; align-self: center; justify-self: center; display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: repeat(3, 1fr); gap: 9px; }
.oxo-cell { position: relative; display: grid; place-items: center; border-radius: 8px; background: rgba(8, 13, 26, 0.82); box-shadow: inset 0 0 0 1px rgba(110, 140, 200, 0.28); }
.oxo-cell small { position: absolute; top: 6px; left: 0; right: 0; color: #93a7d1; font-size: clamp(10px, 1vw, 14px); font-weight: 900; letter-spacing: 0.08em; text-align: center; }
.oxo-cell strong { color: var(--mark); font-size: clamp(34px, 4.6vw, 68px); font-weight: 950; line-height: 1; text-shadow: 0 0 26px color-mix(in srgb, var(--mark) 80%, transparent); }
.oxo-cell.open small { top: 50%; transform: translateY(-50%); color: #c7d6f5; font-size: clamp(15px, 1.7vw, 24px); text-align: center; }
.oxo-cell em { position: absolute; bottom: 7px; left: 0; right: 0; color: #ffd98a; font-size: clamp(8px, 0.8vw, 11px); font-style: normal; font-weight: 900; letter-spacing: 0.12em; text-align: center; }
.oxo-cell.aim { box-shadow: inset 0 0 0 2px #f2c14e, 0 0 22px rgba(242, 193, 78, 0.35); }
.oxo-cell.aim small { color: #ffd98a; }
.oxo-cell.win { box-shadow: inset 0 0 0 2px var(--mark), 0 0 26px color-mix(in srgb, var(--mark) 55%, transparent); animation: oxo-win-pulse 1.1s ease-in-out infinite; }
@keyframes oxo-win-pulse { 50% { filter: brightness(1.45); } }
.oxo-boardcol { min-width: 0; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.oxo-lastdart { padding: 6px 16px; display: flex; align-items: center; gap: 9px; border-radius: 99px; background: rgba(5, 9, 18, 0.9); box-shadow: inset 0 0 0 1px rgba(143, 184, 255, 0.3); }
.oxo-lastdart small { color: #7d90ba; font-size: 8px; font-weight: 900; letter-spacing: 0.1em; }
.oxo-lastdart strong { color: #8fb8ff; font-family: ui-monospace, monospace; font-size: 14px; }

.mode-oxo .oxo-side { padding: 39px 10px 10px; }
.oxo-versus { position: relative; display: flex; flex-direction: column; gap: 8px; }
.oxo-player { display: grid; grid-template-columns: 44px 1fr auto; gap: 9px; align-items: center; padding: 9px 10px; border-radius: 6px; background: rgba(8, 13, 26, 0.8); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--mark) 35%, transparent); opacity: 0.75; }
.oxo-player.current { opacity: 1; box-shadow: inset 0 0 0 2px var(--mark), 0 0 18px color-mix(in srgb, var(--mark) 30%, transparent); }
.oxo-player img { width: 44px; height: 44px; border-radius: 50%; object-fit: cover; border: 2px solid var(--mark); }
.oxo-player div { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.oxo-player strong { overflow: hidden; color: #f0f4ff; font-size: 13px; text-overflow: ellipsis; text-transform: uppercase; white-space: nowrap; }
.oxo-player small { color: #93a7d1; font-size: 10px; font-weight: 800; }
.oxo-player b { color: var(--mark); font-size: 30px; font-weight: 950; text-shadow: 0 0 16px color-mix(in srgb, var(--mark) 70%, transparent); }
.oxo-vs { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); padding: 3px 9px; border-radius: 99px; background: #0a0f1e; box-shadow: inset 0 0 0 1px rgba(143, 184, 255, 0.4); color: #8fb8ff; font-size: 10px; font-weight: 950; letter-spacing: 0.1em; }

.oxo-logo { margin-bottom: 10px; padding: 12px 10px; display: flex; flex-direction: column; align-items: center; gap: 2px; border-radius: 4px; background: linear-gradient(180deg, rgba(43, 66, 120, 0.35), rgba(6, 10, 22, 0.6)); box-shadow: inset 0 0 0 1px rgba(143, 184, 255, 0.25); }
.oxo-logo span { color: #93a7d1; font-size: 9px; font-weight: 900; letter-spacing: 0.24em; }
.oxo-logo strong { color: #dce8ff; font-size: 38px; font-weight: 950; line-height: 1; }
.oxo-logo strong i { font-style: normal; color: var(--mark); text-shadow: 0 0 18px color-mix(in srgb, var(--mark) 70%, transparent); }
.oxo-logo small { color: #93a7d1; font-size: 9px; font-weight: 800; letter-spacing: 0.14em; }
.mode-oxo .arena-rules p::before { width: 6px; height: 6px; border-radius: 1px; background: #8fb8ff; box-shadow: 0 0 8px rgba(143, 184, 255, 0.8); }

@media (prefers-reduced-motion: reduce) {
  .oxo-cell.win { animation: none; }
}

/* ------------------------------------------------ winner celebration */
.winner-celebration { position: fixed; inset: 0; z-index: 120; padding: 24px; display: grid; place-items: center; overflow: hidden; background: radial-gradient(circle at 50% 42%, rgba(132, 46, 205, 0.34), transparent 38%), rgba(2, 3, 9, 0.9); backdrop-filter: blur(12px); }
.winner-panel { width: min(700px, calc(100vw - 40px)); padding: 38px 48px 42px; position: relative; z-index: 2; display: flex; flex-direction: column; align-items: center; justify-content: center; overflow: hidden; border: 2px solid rgba(255, 222, 91, 0.88); border-radius: 28px; background: radial-gradient(circle at 50% 8%, rgba(255, 211, 72, 0.16), transparent 27%), linear-gradient(155deg, rgba(36, 13, 57, 0.97), rgba(8, 10, 19, 0.98) 62%); box-shadow: 0 50px 180px rgba(0, 0, 0, 0.9), 0 0 75px rgba(213, 68, 255, 0.25); animation: winner-panel-enter 0.7s cubic-bezier(0.16, 1.25, 0.3, 1) both; }
.winner-celebration.space-defeat .winner-panel { border-color: #ff4f68; background: radial-gradient(circle at 50% 12%, rgba(255, 79, 104, 0.19), transparent 32%), linear-gradient(155deg, #32101b, #080a10 68%); }
.winner-crown { width: 64px; height: 64px; margin-bottom: 10px; display: grid; place-items: center; border: 2px solid #ffe56b; border-radius: 50%; background: linear-gradient(145deg, #ffeb78, #e79618); color: #3a1900; font-size: 32px; box-shadow: 0 0 35px rgba(255, 216, 79, 0.48); animation: winner-crown 1.7s ease-in-out infinite; }
.winner-celebration.space-defeat .winner-crown { border-color: #ff4f68; background: #551522; color: #fff; }
.winner-photo { width: 150px; height: 150px; margin-bottom: 16px; object-fit: cover; border: 6px solid #111522; border-radius: 50%; background: #111522; box-shadow: 0 0 0 8px rgba(255, 255, 255, 0.08), 0 0 65px rgba(236, 70, 255, 0.38); }
.winner-kicker { margin: 0 0 7px; color: #ffe56b; font-size: 12px; font-weight: 950; letter-spacing: 0.28em; }
.winner-name { max-width: 100%; color: white; font-size: clamp(30px, 5vw, 56px); line-height: 1; letter-spacing: -0.035em; text-align: center; text-transform: uppercase; text-shadow: 0 4px 0 #6d187f, 0 0 30px rgba(244, 85, 255, 0.42); }
.winner-celebration h2 { margin: 4px 0 6px; background: linear-gradient(90deg, #fff3a0, #ffd52f 35%, #fff8c7 52%, #f5a724 78%, #fff3a0); background-size: 220% auto; color: transparent; font-size: clamp(48px, 8vw, 92px); font-style: italic; font-weight: 1000; line-height: 0.96; letter-spacing: -0.06em; text-align: center; -webkit-background-clip: text; background-clip: text; filter: drop-shadow(0 6px 0 #6c2b03) drop-shadow(0 0 22px rgba(255, 206, 57, 0.42)); animation: winner-title-shine 2.8s linear infinite; }
.winner-game-label { margin: 4px 0 14px; color: #cfbce1; font-size: 11px; font-weight: 900; letter-spacing: 0.22em; }
.winner-places { margin: 0 0 20px; padding: 0; list-style: none; color: #cfd6e2; font-size: 13px; text-align: center; }
.winner-places li { padding: 2px 0; }
.winner-actions { width: 100%; display: grid; grid-template-columns: 1fr 1fr; gap: 11px; }
.winner-actions .abtn { min-height: 50px; font-size: 12px; }
.winner-primary { border-color: #ffe66e !important; background: linear-gradient(135deg, #f2a71d, #d23fdc) !important; }
.winner-confetti { position: absolute; inset: 0; z-index: 3; overflow: hidden; pointer-events: none; }
.winner-confetti i { width: 10px; height: 18px; position: absolute; top: -12vh; left: var(--x); border-radius: 2px; background: var(--confetti); box-shadow: 0 0 8px color-mix(in srgb, var(--confetti) 58%, transparent); animation: winner-confetti-fall var(--duration) linear var(--delay) infinite; }
.winner-confetti i:nth-child(3n) { width: 13px; height: 13px; border-radius: 50%; }
.winner-confetti i:nth-child(4n) { width: 7px; height: 23px; }

@keyframes winner-panel-enter { from { opacity: 0; transform: scale(0.74) translateY(45px); } to { opacity: 1; transform: scale(1) translateY(0); } }
@keyframes winner-crown { 50% { transform: translateY(-7px) rotate(4deg); } }
@keyframes winner-title-shine { to { background-position: 220% center; } }
@keyframes winner-confetti-fall { from { transform: translate3d(0, -12vh, 0) rotate(0); } to { transform: translate3d(var(--drift), 118vh, 0) rotate(var(--spin)); } }

/* ------------------------------------------------ dart-removal mode */
/* Sits over the board in every layout - all three stages are position:relative
   already - so the instruction is where the players are looking. */

/* ------------------------------------------------ takeout */
.takeout-banner {
  position: fixed;
  top: 50%;
  left: 50%;
  z-index: 115;
  padding: 14px 34px;
  border: 2px solid #ff5f69;
  border-radius: 10px;
  background: rgba(38, 6, 12, 0.92);
  color: #ffd7da;
  font-size: 22px;
  font-weight: 950;
  letter-spacing: 0.18em;
  pointer-events: none;
  box-shadow: 0 0 45px rgba(255, 95, 105, 0.45);
  transform: translate(-50%, -50%);
  animation: takeout-flash 1.6s ease-out both;
}

@keyframes takeout-flash {
  0% { opacity: 0; transform: translate(-50%, -50%) scale(0.86); }
  12%, 62% { opacity: 1; transform: translate(-50%, -50%) scale(1); }
  100% { opacity: 0; transform: translate(-50%, -50%) scale(1.03); }
}

@media (prefers-reduced-motion: reduce) {
  .takeout-banner { animation-duration: 0.001ms; animation-iteration-count: 1; }
}

/* ------------------------------------------------ dialogs */
.dialog-backdrop { position: fixed; inset: 0; z-index: 110; display: grid; place-items: center; padding: 20px; background: rgba(0, 0, 0, 0.78); backdrop-filter: blur(6px); overflow-y: auto; }
.dialog { width: min(560px, 100%); max-height: calc(100vh - 40px); overflow-y: auto; padding: 26px; position: relative; border: 1px solid #40506a; border-radius: 15px; background: #101722; color: white; box-shadow: 0 40px 120px black; }
.dialog h2 { margin: 0 0 6px; }
.dialog-intro, .dialog-copy { margin: 8px 0 14px; color: #96a0b5; font-size: 13px; line-height: 1.6; }
.dialog-copy strong { color: #e8ebf2; }
.dialog-close { position: absolute; top: 12px; right: 12px; width: 30px; height: 30px; border: 0; border-radius: 50%; background: #263244; color: white; font-size: 18px; cursor: pointer; }
.correction-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 14px; }
.correction-actions button { padding: 12px; border: 1px solid #333c4f; border-radius: 8px; background: #0b1119; color: #c7d0dc; font: inherit; font-size: 12px; font-weight: 800; cursor: pointer; }
.correction-actions button.active { border-color: #38d9f1; background: rgba(56, 217, 241, 0.14); color: white; }
.correction-actions button:disabled { opacity: 0.32; cursor: default; }
.picker-wrap { padding: 15px; border: 1px solid #333c4f; border-radius: 11px; background: radial-gradient(circle at 50% 46%, #1a2230, #080d13 70%); }
.picker-copy { margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.picker-copy small { color: #38d9f1; font-size: 9px; font-weight: 900; letter-spacing: 0.13em; }
.picker-copy span { color: #96a0b5; font-size: 10px; }
.correction-summary { margin: 13px 0; padding: 11px 14px; display: flex; align-items: center; justify-content: space-between; border: 1px solid #333c4f; border-radius: 9px; background: #151e2a; }
.correction-summary span { color: #96a0b5; font-size: 9px; font-weight: 900; letter-spacing: 0.13em; }
.correction-summary strong { color: #38d9f1; font-size: 18px; }
.dialog-footer { display: flex; justify-content: end; gap: 8px; }

/* ------------------------------------------------ fullscreen + presentation */
.presentation-exit { position: fixed; top: 12px; right: 12px; z-index: 150; padding: 8px 12px; border: 1px solid rgba(255, 255, 255, 0.25); border-radius: 8px; background: rgba(6, 12, 20, 0.78); color: white; font-size: 11px; font-weight: 800; cursor: pointer; }

body.presentation-mode .arena-page { height: 100%; display: flex; flex-direction: column; }
body.presentation-mode .arena-titlebar { margin-bottom: 8px; }
body.presentation-mode .game-panel { min-height: 0; flex: 1 1 auto; display: flex; flex-direction: column; }
body.presentation-mode .arena-layout { height: 100%; min-height: 0; flex: 1 1 auto; }
body.presentation-mode .derby-live-layout,
body.presentation-mode .chore-live-layout,
body.presentation-mode .snl-live-layout { height: 100%; min-height: 0; flex: 1 1 auto; }

body.fullscreen-game .arena-page { height: 100%; display: flex; flex-direction: column; }
body.fullscreen-game .arena-titlebar { min-height: 48px; margin-bottom: 8px; flex: 0 0 auto; }
body.fullscreen-game .arena-title h1 { font-size: 21px; }
body.fullscreen-game .game-panel { min-height: 0; flex: 1 1 auto; display: flex; flex-direction: column; overflow: hidden; }
body.fullscreen-game .arena-layout { height: 100%; min-height: 0; flex: 1 1 auto; }
body.fullscreen-game .derby-live-layout,
body.fullscreen-game .chore-live-layout,
body.fullscreen-game .snl-live-layout { height: 100%; min-height: 0; flex: 1 1 auto; }
body.fullscreen-game .stage-board { width: min(90%, calc(100vh - 205px), 820px); }
body.fullscreen-game .space-playfield .space-orbit-stage { width: min(100%, 78vh, 800px); }

/* ------------------------------------------------ responsive */
@media (max-width: 1370px) {
  .arena-layout { grid-template-columns: 180px minmax(300px, 1fr) 225px 245px; }
  .space-layout { grid-template-columns: 165px minmax(320px, 1fr) 205px 225px; }
  .x01-logo strong { font-size: 52px; }
  .space-logo strong { font-size: 36px; }
  .space-logo b { font-size: 30px; }
  .derby-live-layout { grid-template-columns: minmax(520px, 1fr) 270px; }
  .chore-live-layout { grid-template-columns: minmax(520px, 1fr) 250px; }
  .snl-live-layout { grid-template-columns: minmax(460px, 1fr) 280px; }
}

@media (max-width: 1100px) {
  .arena-layout, .space-layout { grid-template-columns: 1fr 1fr; }
  .x01-stage, .killer-target, .space-playfield { grid-column: 1 / -1; order: -1; min-height: 420px; }
  .arena-rules { grid-column: 1 / -1; }
  .derby-live-layout, .chore-live-layout, .snl-live-layout { grid-template-columns: 1fr; }
  .derby-console, .chore-console, .snl-console { overflow: visible; }
}
</style>
