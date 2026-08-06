<script setup>
// Phone-side game controls: the same four actions the main screen has, sized
// for a thumb, plus the zoomable board for correcting a dart.
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import PhoneBoardPicker from './PhoneBoardPicker.vue'
import TurnCorrections from './TurnCorrections.vue'

const props = defineProps({
  state: { type: Object, required: true },
})
const emit = defineEmits(['updated'])

const busy = ref(false)
const error = ref(null)
const overrideOpen = ref(false)
const overrideAction = ref('add')     // 'replace' | 'add'
const overrideTarget = ref('MISS')

const game = computed(() => props.state.game ?? {})
const players = computed(() => props.state.players ?? [])
const current = computed(() => players.value.find((p) => p.player_id === props.state.current_player_id))
const darts = computed(() => props.state.darts_this_turn ?? [])
const perTurn = computed(() => props.state.darts_per_turn ?? 3)
const dartNumber = computed(() => Math.min(perTurn.value, darts.value.length + 1))
const finished = computed(() => Boolean(props.state.finished))

// What "score" means differs per game, so label it rather than show a bare number.
const scoreLabel = computed(() => {
  if (game.value.kind === 'x01') return 'REMAINING'
  if (game.value.kind === 'killer') return 'LIVES'
  if (game.value.kind === 'invaders') return 'POINTS'
  if (game.value.kind === 'golf') return 'STROKES'
  if (game.value.kind === 'oxo') return 'SQUARES'
  return 'SCORE'
})

async function act(fn) {
  busy.value = true
  error.value = null
  try {
    emit('updated', await fn())
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

const undo = () => act(api.undoDart)
// Mirrors the main screen: confirmTakeout only while the darts are actually
// still in the board. Once detection has advanced the turn, that call rewinds
// to the prompt and re-advances, landing back where it started - so there this
// is a plain "advance the turn". See PlayView's dartsRemoved.
const nextPlayer = () =>
  act(props.state.awaiting_takeout ? api.confirmTakeout : api.nextTurn)
const previousPlayer = () => act(api.previousTurn)
const recordMiss = () => act(() => api.sendManualDart(null, 0))

// Detection says the darts are out and has already advanced the turn. Nothing
// is being asked of anyone here - the only reason to surface it at all is so a
// wrong call can be put back.
const takeoutDetected = computed(
  () => !finished.value && !props.state.awaiting_takeout && Boolean(props.state.takeout_override),
)
// Only worth offering once something could actually need putting right: mid
// turn there is nothing to correct that the four main actions don't cover.
const fixOpen = ref(false)
watch(takeoutDetected, (is) => { if (!is) fixOpen.value = false })

function parseTarget(target) {
  if (target === 'MISS') return { segment: null, multiplier: 0 }
  if (target === '25') return { segment: 25, multiplier: 1 }
  if (target === 'BULL') return { segment: 25, multiplier: 2 }
  const m = /^([SDT])(\d+)$/.exec(target)
  if (!m) return { segment: null, multiplier: 0 }
  return { segment: Number(m[2]), multiplier: { S: 1, D: 2, T: 3 }[m[1]] }
}

function openOverride() {
  const last = darts.value.at(-1)
  overrideAction.value = last ? 'replace' : 'add'
  overrideTarget.value = /^(?:[SDT](?:[1-9]|1\d|20)|25|BULL|MISS)$/.test(last?.label || '') ? last.label : 'MISS'
  error.value = null
  overrideOpen.value = true
}

async function applyOverride() {
  const { segment, multiplier } = parseTarget(overrideTarget.value)
  busy.value = true
  error.value = null
  try {
    // "Replace" is an undo followed by the corrected dart - the engine rebuilds
    // the match by replay, so this is exact rather than state surgery.
    if (overrideAction.value === 'replace' && darts.value.length) await api.undoDart()
    emit('updated', await api.sendManualDart(segment, multiplier))
    overrideOpen.value = false
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="controls">
    <!-- whose turn, and what they've thrown -->
    <section class="card turn-card">
      <div class="turn-head">
        <div>
          <small>NOW THROWING</small>
          <strong>{{ current?.name ?? '—' }}</strong>
        </div>
        <div class="turn-score">
          <small>{{ scoreLabel }}</small>
          <b>{{ current?.score ?? 0 }}</b>
        </div>
      </div>

      <p v-if="state.target_hint" class="hint">{{ state.target_hint }}</p>
      <p v-if="state.message" class="message">{{ state.message }}</p>

      <div class="darts">
        <div v-for="i in perTurn" :key="i" class="dart" :class="{ filled: darts[i - 1] }">
          <strong>{{ darts[i - 1]?.label ?? '—' }}</strong>
          <span>{{ darts[i - 1] ? `${darts[i - 1].score} pts` : `dart ${i}` }}</span>
        </div>
      </div>

      <p class="phase" :class="{ warn: state.awaiting_takeout, ok: takeoutDetected, done: finished }">
        {{ finished ? 'GAME FINISHED'
          : takeoutDetected ? `${current?.name ?? 'NEXT PLAYER'} IS UP`
          : state.awaiting_takeout ? 'TURN OVER — REMOVE THE DARTS'
          : `DART ${dartNumber} OF ${perTurn}` }}
      </p>

      <!-- Detection got it right, so this stays out of the way - but the
           corrections are one tap away for the times it did not. -->
      <button v-if="takeoutDetected" class="fix-toggle" :disabled="busy" @click="fixOpen = !fixOpen">
        {{ fixOpen ? 'Close' : 'Turn changed by mistake? Fix it' }}
      </button>
      <div v-if="takeoutDetected && fixOpen" class="fix-panel">
        <TurnCorrections
          compact
          :state="state"
          :busy="busy"
          @undo="undo"
          @miss="recordMiss"
          @previous="previousPlayer"
        />
      </div>
    </section>

    <p v-if="error" class="status error">{{ error }}</p>

    <!-- the four actions -->
    <div class="action-grid">
      <button class="act override" :disabled="busy" @click="openOverride">
        <span class="act-title">Override / miss</span>
        <span class="act-sub">Correct a dart on the board</span>
      </button>
      <button class="act miss" :disabled="busy || finished" @click="recordMiss">
        <span class="act-title">Record complete miss</span>
        <span class="act-sub">Cameras saw nothing</span>
      </button>
      <button class="act" :disabled="busy || !darts.length" @click="undo">
        <span class="act-title">Undo dart</span>
        <span class="act-sub">{{ darts.length }} of {{ perTurn }} thrown</span>
      </button>
      <button class="act next" :class="{ urgent: state.awaiting_takeout }" :disabled="busy || finished" @click="nextPlayer">
        <span class="act-title">{{ state.awaiting_takeout ? 'Darts removed' : 'Next player' }}</span>
        <span class="act-sub">{{ state.awaiting_takeout ? 'Confirm the board is clear' : 'Advance the turn' }}</span>
      </button>
      <button class="act back" :disabled="busy" @click="previousPlayer">
        <span class="act-title">Previous player</span>
        <span class="act-sub">Undo a turn change</span>
      </button>
    </div>

    <!-- scoreboard -->
    <section class="card">
      <h3>Scores</h3>
      <div
        v-for="p in players"
        :key="p.player_id"
        class="score-row"
        :class="{ current: p.player_id === state.current_player_id, out: p.finished && !finished }"
      >
        <img :src="p.avatar" alt="" />
        <div>
          <strong>{{ p.name }}</strong>
          <span v-if="game.kind === 'killer'">
            {{ (game.killers ?? []).includes(p.player_id) ? 'KILLER' : `${game.marks?.[p.player_id] ?? 0}/3 marks` }}
            · {{ (game.targets?.[p.player_id] ?? []).join(' & ') }}
          </span>
          <span v-else-if="game.kind === 'invaders'">{{ game.kills?.[p.player_id] ?? 0 }} kills</span>
          <span v-else-if="p.place">finished {{ p.place }}</span>
        </div>
        <b>{{ p.score }}</b>
      </div>
    </section>

    <!-- override sheet -->
    <div v-if="overrideOpen" class="sheet-backdrop" @click.self="overrideOpen = false">
      <div class="sheet">
        <div class="sheet-head">
          <h3>Correct a dart</h3>
          <button class="close" @click="overrideOpen = false">×</button>
        </div>

        <div class="mode-row">
          <button
            :class="{ active: overrideAction === 'replace' }"
            :disabled="!darts.length"
            @click="overrideAction = 'replace'"
          >Replace last dart</button>
          <button :class="{ active: overrideAction === 'add' }" @click="overrideAction = 'add'">
            Add missed dart
          </button>
        </div>

        <PhoneBoardPicker :target="overrideTarget" @pick="overrideTarget = $event" />

        <p v-if="error" class="status error">{{ error }}</p>

        <div class="sheet-actions">
          <button class="ghost" @click="overrideOpen = false">Cancel</button>
          <button class="primary" :disabled="busy" @click="applyOverride">
            {{ busy ? 'Saving…' : overrideAction === 'replace' ? 'Replace dart' : 'Add dart' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.controls {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  text-align: left;
}

.turn-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.turn-head small,
.turn-score small {
  display: block;
  color: var(--muted);
  font-size: 0.62rem;
  font-weight: 900;
  letter-spacing: 0.14em;
}

.turn-head strong {
  font-size: 1.3rem;
}

.turn-score {
  text-align: right;
}

.turn-score b {
  font-size: 2rem;
  line-height: 1;
}

.hint {
  margin: 0.5rem 0 0;
  color: var(--accent);
  font-size: 0.85rem;
}

.message {
  margin: 0.35rem 0 0;
  font-size: 0.85rem;
  font-weight: 600;
}

.darts {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.4rem;
  margin-top: 0.7rem;
}

.dart {
  padding: 0.5rem 0.3rem;
  border: 1px dashed var(--border);
  border-radius: 8px;
  text-align: center;
}

.dart.filled {
  border-style: solid;
  border-color: var(--accent);
}

.dart strong {
  display: block;
  font-family: ui-monospace, monospace;
  font-size: 1.05rem;
}

.dart span {
  color: var(--muted);
  font-size: 0.68rem;
}

.phase {
  margin: 0.6rem 0 0;
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.1em;
  text-align: center;
}

.phase.warn {
  color: #ffbf4d;
}

.phase.ok {
  color: var(--accent);
}

.act.next.urgent {
  border-color: #ffbf4d;
  background: rgba(255, 191, 77, 0.16);
}

.phase.done {
  color: var(--accent);
}

.action-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem;
}

.act {
  min-height: 74px;
  padding: 0.6rem;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 0.15rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel-2);
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.act:disabled {
  opacity: 0.4;
}

.act-title {
  font-size: 0.92rem;
  font-weight: 700;
}

.act-sub {
  color: var(--muted);
  font-size: 0.68rem;
}

.act.override {
  border-color: #b0459d;
  background: rgba(176, 69, 157, 0.15);
}

.act.miss {
  border-color: #e05555;
  background: rgba(224, 85, 85, 0.12);
}

.act.next {
  border-color: var(--accent);
  background: rgba(56, 178, 110, 0.12);
}

.act.back {
  grid-column: 1 / -1;
  min-height: 58px;
  border-color: rgba(255, 191, 77, 0.5);
  background: rgba(255, 191, 77, 0.1);
}

.fix-toggle {
  width: 100%;
  margin-top: 0.5rem;
  padding: 0.6rem;
  border: 1px solid rgba(87, 220, 139, 0.5);
  border-radius: 9px;
  background: rgba(56, 178, 110, 0.12);
  color: var(--text);
  font: inherit;
  font-size: 0.8rem;
  font-weight: 700;
  cursor: pointer;
}

.fix-toggle:disabled {
  opacity: 0.4;
}

.fix-panel {
  margin-top: 0.5rem;
  padding-top: 0.6rem;
  border-top: 1px solid var(--border);
}

.score-row {
  display: grid;
  grid-template-columns: 34px 1fr auto;
  gap: 0.55rem;
  align-items: center;
  padding: 0.4rem 0;
  border-top: 1px solid var(--border);
}

.score-row:first-of-type {
  border-top: 0;
}

.score-row.current {
  color: var(--text);
}

.score-row.current strong {
  color: var(--accent);
}

.score-row.out {
  opacity: 0.45;
}

.score-row img {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  object-fit: cover;
}

.score-row div {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.score-row strong {
  font-size: 0.92rem;
}

.score-row span {
  color: var(--muted);
  font-size: 0.7rem;
}

.score-row b {
  font-family: ui-monospace, monospace;
  font-size: 1.2rem;
}

.sheet-backdrop {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  align-items: flex-end;
  background: rgba(0, 0, 0, 0.75);
}

.sheet {
  width: 100%;
  max-height: 94vh;
  overflow-y: auto;
  padding: 0.9rem 0.9rem 1.2rem;
  border-radius: 16px 16px 0 0;
  background: var(--panel);
  border-top: 1px solid var(--border);
}

.sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.6rem;
}

.sheet-head h3 {
  margin: 0;
}

.close {
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 50%;
  background: var(--panel-2);
  color: var(--text);
  font-size: 1.2rem;
  cursor: pointer;
}

.mode-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.4rem;
  margin-bottom: 0.7rem;
}

.mode-row button {
  padding: 0.6rem 0.4rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 0.82rem;
  cursor: pointer;
}

.mode-row button.active {
  border-color: var(--accent);
  color: var(--text);
  background: rgba(56, 178, 110, 0.12);
}

.mode-row button:disabled {
  opacity: 0.35;
}

.sheet-actions {
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  gap: 0.5rem;
  margin-top: 0.8rem;
}

.sheet-actions button {
  padding: 0.85rem;
}
</style>
