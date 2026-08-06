<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'

// The whole page renders from one status object, which is also exactly what
// the backend pushes over the WebSocket. One shape, one renderer - there is
// never a moment where a polled value and a pushed value disagree.
const status = ref(null)
const error = ref('')
const busy = ref(false)
const restarting = ref(false)

let ws = null
let reconnectTimer = null

const state = computed(() => status.value?.state || 'idle')
const available = computed(() => status.value?.available || null)
const pending = computed(() => status.value?.pending || null)
const lastResult = computed(() => status.value?.last_result || null)

const progressPercent = computed(() => Math.round((status.value?.progress || 0) * 100))

function formatBytes(bytes) {
  if (!bytes) return '0 KB'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatWhen(value) {
  if (!value) return ''
  const date = new Date(typeof value === 'number' ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

async function call(fn) {
  error.value = ''
  busy.value = true
  try {
    status.value = { ...status.value, ...(await fn()) }
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

const refresh = () => call(api.getUpdateStatus)
const check = () => call(api.checkForUpdate)
const download = () => call(api.downloadUpdate)
const cancel = () => call(api.cancelUpdate)
const discard = () => call(api.discardUpdate)

async function restart() {
  error.value = ''
  restarting.value = true
  try {
    await api.restartForUpdate()
    // The server is about to exit, so nothing it says after this arrives.
    // Poll until it answers again, then reload onto the new version.
    waitForServer()
  } catch (err) {
    error.value = err.message
    restarting.value = false
  }
}

function waitForServer() {
  let attempts = 0
  const tick = async () => {
    attempts += 1
    try {
      const res = await fetch('/api/update/status', { cache: 'no-store' })
      if (res.ok) {
        window.location.reload()
        return
      }
    } catch {
      /* still down, which is expected */
    }
    // Generous: applying the update plus importing OpenCV on a Pi is slow,
    // and giving up early would tell the user it failed when it had not.
    if (attempts < 60) setTimeout(tick, 2000)
    else {
      restarting.value = false
      error.value =
        'The app is taking longer than expected to come back. Refresh this page in a moment.'
    }
  }
  setTimeout(tick, 3000)
}

async function setChannel(event) {
  await call(() => api.saveUpdateSettings({ channel: event.target.value }))
  await check()
}

async function setAutoCheck(event) {
  await call(() => api.saveUpdateSettings({ auto_check: event.target.checked }))
}

function connect() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${window.location.host}/ws`)
  ws.onmessage = (evt) => {
    let msg
    try { msg = JSON.parse(evt.data) } catch { return }
    if (msg.type === 'update.status') status.value = { ...status.value, ...msg }
  }
  ws.onclose = () => {
    ws = null
    if (!restarting.value) reconnectTimer = setTimeout(connect, 2000)
  }
}

onMounted(() => { refresh(); connect() })
onBeforeUnmount(() => { clearTimeout(reconnectTimer); ws?.close() })
</script>

<template>
  <section class="updates">
    <h1>Updates</h1>

    <div v-if="lastResult" class="banner" :class="lastResult.status">
      <strong v-if="lastResult.status === 'applied'">
        Updated to version {{ lastResult.version }}.
      </strong>
      <strong v-else-if="lastResult.status === 'rolled_back'">
        Version {{ lastResult.version }} would not start, so the previous version was restored.
      </strong>
      <strong v-else>Last update attempt failed.</strong>
      <span v-if="lastResult.detail"> {{ lastResult.detail }}</span>
    </div>

    <div class="card">
      <div class="current">
        <div>
          <span class="label">Installed version</span>
          <span class="version">{{ status?.current_version || '—' }}</span>
        </div>
        <div class="right">
          <span class="label">Last checked</span>
          <span>{{ formatWhen(status?.last_check) || 'never' }}</span>
        </div>
      </div>

      <p v-if="!status?.can_update" class="blocked">{{ status?.reason }}</p>

      <template v-else>
        <p v-if="state === 'up_to_date'" class="ok">{{ status.message }}</p>
        <p v-else-if="state === 'error'" class="bad">{{ status.message }}</p>
        <p v-else-if="state === 'checking'">Checking for updates…</p>

        <div v-if="available && state !== 'up_to_date'" class="release">
          <h2>Version {{ available.version }} is available</h2>
          <p class="meta">
            Released {{ formatWhen(available.released) }} ·
            {{ available.changed_files }} of {{ available.file_count }} files changed ·
            <strong>{{ formatBytes(available.download_bytes) }}</strong> to download
          </p>
          <pre v-if="available.notes" class="notes">{{ available.notes }}</pre>
        </div>

        <div v-if="state === 'downloading'" class="progress">
          <div class="bar"><div class="fill" :style="{ width: progressPercent + '%' }" /></div>
          <span>
            {{ progressPercent }}% ·
            {{ formatBytes(status.downloaded_bytes) }} of
            {{ formatBytes(status.download_total_bytes) }}
          </span>
        </div>

        <div v-if="state === 'ready' || pending" class="ready">
          <strong>Version {{ pending?.version }} is downloaded and ready.</strong>
          <p>
            It installs when the app restarts, which takes a few seconds.
            Nothing changes until you press the button — so finish your game first.
          </p>
        </div>

        <div class="actions">
          <button v-if="state !== 'downloading' && !pending" :disabled="busy" @click="check">
            Check for updates
          </button>
          <button
            v-if="available && state === 'available'"
            class="primary"
            :disabled="busy"
            @click="download"
          >
            Download {{ available.version }} ({{ formatBytes(available.download_bytes) }})
          </button>
          <button v-if="state === 'downloading'" @click="cancel">Cancel download</button>
          <button v-if="pending" class="primary" :disabled="restarting" @click="restart">
            {{ restarting ? 'Restarting…' : 'Install and restart' }}
          </button>
          <button v-if="pending && !restarting" class="quiet" :disabled="busy" @click="discard">
            Discard
          </button>
        </div>
      </template>

      <p v-if="error" class="bad">{{ error }}</p>
    </div>

    <div class="card settings">
      <h2>Settings</h2>
      <label>
        <span>Release channel</span>
        <select :value="status?.channel" @change="setChannel">
          <option value="stable">Stable — tested releases</option>
          <option value="beta">Beta — try new versions first</option>
        </select>
      </label>
      <label class="check">
        <input
          type="checkbox"
          :checked="status?.auto_check !== false"
          @change="setAutoCheck"
        />
        <span>Check for updates when the app starts</span>
      </label>
      <p class="hint">
        Updates are only ever downloaded and installed when you ask. Your players,
        calibration and settings are kept.
      </p>
    </div>
  </section>
</template>

<style scoped>
.updates { max-width: 720px; }
h1 { margin-bottom: 1rem; }
h2 { font-size: 1.05rem; margin: 0 0 0.35rem; }

.card {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  padding: 1.1rem 1.25rem;
  margin-bottom: 1rem;
}

.current { display: flex; justify-content: space-between; gap: 1rem; margin-bottom: 0.75rem; }
.current > div { display: flex; flex-direction: column; gap: 0.15rem; }
.right { text-align: right; }
.label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.6; }
.version { font-size: 1.5rem; font-weight: 600; }

.release { margin: 0.75rem 0; }
.meta { font-size: 0.85rem; opacity: 0.75; margin: 0.2rem 0 0.5rem; }
.notes {
  white-space: pre-wrap;
  font-family: inherit;
  background: rgba(0, 0, 0, 0.25);
  border-radius: 6px;
  padding: 0.6rem 0.75rem;
  margin: 0;
  font-size: 0.9rem;
}

.progress { margin: 0.75rem 0; display: flex; flex-direction: column; gap: 0.35rem; }
.bar { height: 8px; border-radius: 4px; background: rgba(255, 255, 255, 0.12); overflow: hidden; }
.fill { height: 100%; background: #22c55e; transition: width 0.2s ease; }
.progress span { font-size: 0.85rem; opacity: 0.8; }

.ready {
  background: rgba(34, 197, 94, 0.12);
  border: 1px solid rgba(34, 197, 94, 0.35);
  border-radius: 8px;
  padding: 0.7rem 0.9rem;
  margin: 0.75rem 0;
}
.ready p { margin: 0.3rem 0 0; font-size: 0.9rem; opacity: 0.85; }

.actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.9rem; }
button.primary { background: #22c55e; color: #04210f; font-weight: 600; }
button.quiet { opacity: 0.7; }

.banner { border-radius: 8px; padding: 0.7rem 0.9rem; margin-bottom: 1rem; font-size: 0.92rem; }
.banner.applied { background: rgba(34, 197, 94, 0.15); border: 1px solid rgba(34, 197, 94, 0.4); }
.banner.rolled_back,
.banner.failed { background: rgba(234, 88, 12, 0.15); border: 1px solid rgba(234, 88, 12, 0.45); }

.ok { color: #4ade80; }
.bad { color: #f87171; }
.blocked { opacity: 0.7; font-size: 0.92rem; }

.settings label { display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.75rem; }
.settings label.check { flex-direction: row; align-items: center; gap: 0.5rem; }
.hint { font-size: 0.85rem; opacity: 0.7; margin: 0; }
</style>
