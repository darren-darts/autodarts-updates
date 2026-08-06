<script setup>
// Phone LED tab: fire an effect, preview the game cues, and set the resting
// colour. Deliberately effect-first rather than settings-first - the transport
// wiring is a one-time setup job that belongs on the desktop page, while
// "make the lights do something" is what you want from a phone at a party.
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const status = ref(null)
const effects = ref([])
const cues = ref({})
const error = ref(null)
const note = ref(null)
const busy = ref(false)

const fx = ref('RAINBOW')
const colour = ref('#ff3200')
const speed = ref(20)
const brightness = ref(200)

// The handful worth reaching for at a party, if the firmware has them.
const FAVOURITES = ['RAINBOW', 'CELEBRATION', 'FIRE', 'COMET', 'POLICE', 'BULLSEYE', 'WAVE', 'SOLID']
const quickEffects = computed(() => {
  const names = new Set(effects.value.map((e) => e.name))
  return FAVOURITES.filter((name) => names.has(name))
})

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function flash(text) {
  note.value = text
  setTimeout(() => { if (note.value === text) note.value = null }, 2200)
}

async function refresh() {
  try {
    const [st, list, cu] = await Promise.all([api.getLedStatus(), api.getLedEffects(), api.getLedCues()])
    status.value = st
    effects.value = list.effects
    cues.value = cu.cues
  } catch (err) {
    error.value = err.message
  }
}

async function send(state, text) {
  busy.value = true
  error.value = null
  try {
    await api.sendLedState(state)
    flash(text)
    setTimeout(refresh, 400)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

const sendCurrent = () => send({
  on: true,
  fx: fx.value,
  col: hexToRgb(colour.value),
  sx: Number(speed.value),
  bri: Number(brightness.value),
}, `Sent ${fx.value}`)

const pickEffect = (name) => { fx.value = name; return sendCurrent() }
const allOff = () => send({ on: false }, 'Lights off')
const restingWhite = () => send({ on: true, fx: 'SOLID', col: [255, 255, 255], bri: 255 }, 'Back to white')

async function fireCue(name) {
  busy.value = true
  error.value = null
  try {
    await api.fireLedCue(name)
    flash(`Fired ${name}`)
    setTimeout(refresh, 400)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="leds">
    <p v-if="status" class="status" :class="status.connected ? 'ok' : 'error'">
      {{ status.connected
        ? `Connected via ${status.active_transport} (${status.target})`
        : (status.last_error ?? 'Not connected — send an effect to try') }}
    </p>
    <p v-if="error" class="status error">{{ error }}</p>
    <p v-if="note" class="status ok">{{ note }}</p>

    <section class="card">
      <h3>Effects</h3>
      <div class="effect-grid">
        <button
          v-for="name in quickEffects"
          :key="name"
          class="effect"
          :class="{ active: fx === name }"
          :disabled="busy"
          @click="pickEffect(name)"
        >{{ name }}</button>
      </div>

      <label class="field">
        <span>All effects</span>
        <select v-model="fx">
          <option v-for="e in effects" :key="e.id" :value="e.name">{{ e.name }}</option>
        </select>
      </label>

      <div class="row">
        <label class="field colour-field">
          <span>Colour</span>
          <input v-model="colour" type="color" />
        </label>
        <div class="sliders">
          <label class="field">
            <span>Brightness {{ brightness }}</span>
            <input v-model="brightness" type="range" min="0" max="255" />
          </label>
          <label class="field">
            <span>Speed {{ speed }} <small>(lower = faster)</small></span>
            <input v-model="speed" type="range" min="0" max="200" />
          </label>
        </div>
      </div>

      <div class="buttons">
        <button class="primary" :disabled="busy" @click="sendCurrent">Send</button>
        <button class="ghost" :disabled="busy" @click="restingWhite">White</button>
        <button class="ghost" :disabled="busy" @click="allOff">Off</button>
      </div>
      <p class="muted foot">
        White at full brightness is the resting state — the cameras are calibrated
        under it, so leave the board there while a game is being scored.
      </p>
    </section>

    <section class="card">
      <h3>Game cues</h3>
      <p class="muted foot">The named effects the app fires during a game. Tap to preview.</p>
      <div class="cue-grid">
        <button v-for="(state, name) in cues" :key="name" class="cue" :disabled="busy" @click="fireCue(name)">
          {{ name }}
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.leds {
  text-align: left;
}

.card {
  margin-bottom: 0.7rem;
}

h3 {
  margin: 0 0 0.6rem;
  font-size: 0.98rem;
}

.effect-grid,
.cue-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.4rem;
}

.effect,
.cue {
  min-height: 46px;
  padding: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--panel-2);
  color: var(--text);
  font: inherit;
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
}

.effect.active {
  border-color: var(--accent);
  color: var(--accent);
}

.effect:disabled,
.cue:disabled {
  opacity: 0.5;
}

.cue {
  font-family: ui-monospace, monospace;
  font-size: 0.74rem;
}

.field {
  display: block;
  margin-top: 0.7rem;
}

.field span {
  display: block;
  margin-bottom: 0.3rem;
  color: var(--muted);
  font-size: 0.78rem;
}

.field span small {
  font-size: 0.68rem;
}

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

.field input[type='range'] {
  width: 100%;
  height: 30px;
}

.row {
  display: grid;
  grid-template-columns: 74px 1fr;
  gap: 0.7rem;
  align-items: start;
}

.colour-field input[type='color'] {
  width: 100%;
  height: 52px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
}

.sliders {
  min-width: 0;
}

.buttons {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1fr;
  gap: 0.4rem;
  margin-top: 0.8rem;
}

.buttons button {
  padding: 0.8rem 0.4rem;
}

.foot {
  margin: 0.6rem 0 0;
  font-size: 0.75rem;
  line-height: 1.5;
}
</style>
