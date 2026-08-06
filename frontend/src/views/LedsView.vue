<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const status = ref(null)
const ports = ref([])
const effects = ref([])
const cues = ref({})
const form = ref({ transport: 'auto', serial_port: null, http_url: '' })
const test = ref({ fx: 'RAINBOW', color: '#ff3200', speed: 20, brightness: 80 })
const message = ref(null) // { kind, text }

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

async function refresh() {
  try {
    const [st, po, fx, cu, settings] = await Promise.all([
      api.getLedStatus(),
      api.getLedPorts(),
      api.getLedEffects(),
      api.getLedCues(),
      api.getSettings(),
    ])
    status.value = st
    ports.value = po.ports
    effects.value = fx.effects
    cues.value = cu.cues
    form.value = {
      transport: settings.leds.transport,
      serial_port: settings.leds.serial_port,
      http_url: settings.leds.http_url,
    }
  } catch (err) {
    message.value = { kind: 'error', text: `Failed to load: ${err.message}` }
  }
}

async function saveSettings() {
  message.value = null
  try {
    await api.saveLedSettings(form.value)
    message.value = { kind: 'ok', text: 'LED settings saved.' }
    setTimeout(refresh, 500)
  } catch (err) {
    message.value = { kind: 'error', text: `Save failed: ${err.message}` }
  }
}

async function sendTest() {
  await api.sendLedState({
    on: true,
    fx: test.value.fx,
    col: hexToRgb(test.value.color),
    sx: Number(test.value.speed),
    bri: Number(test.value.brightness),
  })
  setTimeout(refresh, 500)
}

async function fireCue(name) {
  await api.fireLedCue(name)
  setTimeout(refresh, 500)
}

async function off() {
  await api.sendLedState({ on: false })
  setTimeout(refresh, 500)
}

onMounted(refresh)
</script>

<template>
  <div>
    <h1>LED surround</h1>
    <p class="muted">
      The LED ring is driven by an ESP32, over USB serial or WiFi. "Auto"
      prefers a USB connection and falls back to WiFi.
    </p>

    <div class="camera-grid">
      <div class="card">
        <h3>Connection</h3>
        <label class="field">
          <span>Transport</span>
          <select v-model="form.transport">
            <option value="auto">Auto (USB first, then WiFi)</option>
            <option value="serial">USB serial only</option>
            <option value="http">WiFi only</option>
            <option value="off">Off</option>
          </select>
        </label>
        <label class="field" v-if="form.transport === 'auto' || form.transport === 'serial'">
          <span>Serial port</span>
          <select v-model="form.serial_port">
            <option :value="null">auto-detect ESP32</option>
            <option v-for="p in ports" :key="p.device" :value="p.device">
              {{ p.device }} — {{ p.description }}{{ p.likely_esp32 ? ' ★' : '' }}
            </option>
          </select>
        </label>
        <label class="field" v-if="form.transport === 'auto' || form.transport === 'http'">
          <span>HTTP URL</span>
          <input v-model="form.http_url" type="text" />
        </label>
        <div class="actions">
          <button class="primary" @click="saveSettings">Save</button>
          <span v-if="message" class="status" :class="message.kind">{{ message.text }}</span>
        </div>

        <h3 style="margin-top: 1.2rem">Status</h3>
        <p v-if="status" class="status" :class="status.connected ? 'ok' : 'error'">
          {{ status.connected
            ? `connected via ${status.active_transport} (${status.target})`
            : (status.last_error ?? 'not connected yet — send an effect to connect') }}
        </p>
      </div>

      <div class="card">
        <h3>Test an effect</h3>
        <label class="field">
          <span>Effect</span>
          <select v-model="test.fx">
            <option v-for="e in effects" :key="e.id" :value="e.name">{{ e.name }}</option>
          </select>
        </label>
        <label class="field">
          <span>Colour</span>
          <input v-model="test.color" type="color" />
        </label>
        <label class="field">
          <span>Speed (ms/frame, lower = faster): {{ test.speed }}</span>
          <input v-model="test.speed" type="range" min="0" max="200" />
        </label>
        <label class="field">
          <span>Brightness: {{ test.brightness }}</span>
          <input v-model="test.brightness" type="range" min="0" max="255" />
        </label>
        <div class="actions">
          <button class="primary" @click="sendTest">Send</button>
          <button class="ghost" @click="off">Off</button>
        </div>
      </div>

      <div class="card">
        <h3>Game cues</h3>
        <p class="muted">
          Named cues the app fires during calibration and games — tap to preview.
        </p>
        <div class="cue-list">
          <button v-for="(state, name) in cues" :key="name" class="ghost" @click="fireCue(name)">
            {{ name }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.field {
  display: block;
  margin-bottom: 0.75rem;
}

.field span {
  display: block;
  color: var(--muted);
  font-size: 0.85rem;
  margin-bottom: 0.3rem;
}

.field input[type='text'] {
  width: 100%;
  padding: 0.5rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--panel-2);
  color: var(--text);
}

.field input[type='range'] {
  width: 100%;
}

.field input[type='color'] {
  width: 4rem;
  height: 2.2rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel-2);
}

.cue-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
</style>
