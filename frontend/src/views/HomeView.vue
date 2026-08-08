<script setup>
import QRCode from 'qrcode'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { usePlayersStore } from '../stores/players'

const players = usePlayersStore()
const ips = ref([])
const chosenIp = ref(null)
const canvas = ref(null)
const error = ref(null)

// Camera setup/calibration now live in the Autodarts Board Manager (default
// port 3180 on this host), not in InterDarts.
const boardManagerUrl = computed(() => `http://${window.location.hostname}:3180`)

// The main screen's own browser may be pointed at "localhost", which a
// phone on the same network can't reach - so the QR always encodes the
// server's LAN IP instead, keeping the current protocol/port.
const joinUrl = computed(() => {
  if (!chosenIp.value) return null
  const port = window.location.port ? `:${window.location.port}` : ''
  return `${window.location.protocol}//${chosenIp.value}${port}/join`
})

async function refresh() {
  error.value = null
  try {
    const info = await api.getNetworkInfo()
    ips.value = info.ips
    chosenIp.value = info.ips[0] ?? null
    if (info.ips.length === 0) {
      error.value = 'No LAN network address detected - is this machine connected to a network?'
    }
  } catch (err) {
    error.value = `Failed to load network info: ${err.message}`
  }
}

async function renderQr() {
  if (!joinUrl.value || !canvas.value) return
  await QRCode.toCanvas(canvas.value, joinUrl.value, {
    width: 220,
    margin: 1,
    color: { dark: '#e8ebf2', light: '#00000000' },
  })
}

watch(joinUrl, () => nextTick(renderQr))
onMounted(() => {
  refresh()
  players.fetch()
  players.connect()
})
</script>

<template>
  <div>
    <h1>Autodarts</h1>
    <p class="muted">Main screen. Scan the code below on your phone to join.</p>

    <div class="camera-grid">
      <div class="card">
        <h3>Join on your phone</h3>
        <div class="qr-wrap">
          <canvas ref="canvas" width="220" height="220"></canvas>
        </div>
        <p v-if="joinUrl" class="join-url">{{ joinUrl }}</p>
        <p v-if="error" class="status error">{{ error }}</p>
        <label v-if="ips.length > 1" class="field">
          <span>Network address (pick the one your phone shares)</span>
          <select v-model="chosenIp">
            <option v-for="ip in ips" :key="ip" :value="ip">{{ ip }}</option>
          </select>
        </label>
      </div>

      <div class="card">
        <h3>Players</h3>
        <p class="muted">{{ players.players.length }} player{{ players.players.length === 1 ? '' : 's' }} ready.</p>
        <div class="player-preview">
          <img
            v-for="p in players.players"
            :key="p.id"
            :src="p.avatar"
            :alt="p.name"
            :title="p.name"
            class="preview-avatar"
          />
        </div>
        <router-link class="primary nav-card" to="/players">Manage players</router-link>
      </div>

      <div class="card">
        <h3>Setup &amp; tools</h3>
        <p class="muted">Dart cameras and calibration live in the Autodarts Board Manager; the LED surround is configured here.</p>
        <div class="nav-cards">
          <a class="ghost nav-card" :href="boardManagerUrl" target="_blank" rel="noopener">Dart cameras</a>
          <router-link class="ghost nav-card" to="/leds">LEDs</router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qr-wrap {
  display: flex;
  justify-content: center;
  padding: 1rem;
  background: #05070b;
  border-radius: 8px;
}

.join-url {
  text-align: center;
  color: var(--muted);
  font-size: 0.85rem;
  margin: 0.75rem 0 0;
  word-break: break-all;
}

.field {
  display: block;
  margin-top: 1rem;
}

.field span {
  display: block;
  color: var(--muted);
  font-size: 0.85rem;
  margin-bottom: 0.3rem;
}

.field select {
  width: 100%;
  padding: 0.5rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--panel-2);
  color: var(--text);
}

.nav-cards {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.nav-card {
  text-align: left;
  text-decoration: none;
  display: block;
}

.player-preview {
  display: flex;
  gap: 0.5rem;
  margin: 0.75rem 0 1rem;
  flex-wrap: wrap;
}

.preview-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: 2px solid var(--border);
  object-fit: cover;
}
</style>
