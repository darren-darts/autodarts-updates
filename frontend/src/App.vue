<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

// A dot on the Updates tab when something is waiting. The update itself is
// always a deliberate choice, so this is the only unprompted mention of it -
// no dialog, and nothing that could interrupt a game in progress.
const updateReady = ref('')

// A game can be started from a phone, not just from this screen, so the main
// display has to react to one appearing wherever it came from. Only the view
// that started it used to navigate, which left the big screen sitting on the
// games library while a match was already running.
//
// This is a deliberately tiny listener - it only routes. The play screen owns
// its own socket and all the rendering; duplicating that here would mean two
// sources of truth for the same state.
let ws = null

function connect() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${window.location.host}/ws`)
  ws.onmessage = (evt) => {
    let msg
    try { msg = JSON.parse(evt.data) } catch { return }
    if (msg.type === 'update.status') {
      updateReady.value =
        msg.state === 'ready' ? `Version ${msg.pending?.version} is ready to install`
        : msg.state === 'available' ? `Version ${msg.available?.version} is available`
        : ''
      return
    }
    if (msg.type !== 'game.state') return
    // Phones are remotes - they must never be yanked onto the play screen.
    if (route.meta.phone) return
    if (msg.state?.active && route.path !== '/play') router.push('/play')
  }
  ws.onclose = () => { ws = null; setTimeout(connect, 2000) }
}

// The socket only carries *changes*, so a status that settled before this
// screen connected would never arrive. One fetch on load covers that.
async function primeUpdateBadge() {
  try {
    const res = await fetch('/api/update/status')
    if (!res.ok) return
    const msg = await res.json()
    updateReady.value =
      msg.state === 'ready' ? `Version ${msg.pending?.version} is ready to install`
      : msg.state === 'available' ? `Version ${msg.available?.version} is available`
      : ''
  } catch { /* the badge is optional; never let it break the shell */ }
}

onMounted(() => { connect(); primeUpdateBadge() })
onBeforeUnmount(() => ws?.close())
</script>

<template>
  <div>
    <header class="topbar" v-if="!route.meta.phone">
      <router-link to="/" class="brand">Auto<span>darts</span></router-link>
      <nav>
        <router-link to="/games">Games</router-link>
        <router-link to="/players">Players</router-link>
        <router-link to="/setup">Setup</router-link>
        <router-link to="/calibration">Calibration</router-link>
        <router-link to="/detection">Detection</router-link>
        <router-link to="/leds">LEDs</router-link>
        <router-link to="/updates" class="update-link">
          Updates
          <span v-if="updateReady" class="badge" :title="updateReady" />
        </router-link>
      </nav>
    </header>
    <main class="page" :class="{ 'phone-page-shell': route.meta.phone }">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.brand {
  color: inherit;
  text-decoration: none;
}

.phone-page-shell {
  max-width: 480px;
}

.update-link {
  position: relative;
}

.badge {
  position: absolute;
  top: -2px;
  right: -8px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
}
</style>
