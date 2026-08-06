<script setup>
// The phone app. One WebSocket and one poll live here and the game state is
// passed down, so switching tabs doesn't open a second connection per tab.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import PlayerRoster from '../components/PlayerRoster.vue'
import PhoneGameControls from '../components/PhoneGameControls.vue'
import PhoneGameLibrary from '../components/PhoneGameLibrary.vue'
import PhoneLeds from '../components/PhoneLeds.vue'
import { api } from '../api'

const TABS = [
  { key: 'play', label: 'Play', icon: '🎯' },
  { key: 'games', label: 'Games', icon: '🎮' },
  { key: 'players', label: 'Players', icon: '👥' },
  { key: 'leds', label: 'Lights', icon: '💡' },
]

const tab = ref('play')
const state = ref({ active: false })
const presentation = ref(false)
const busy = ref(false)

let ws = null
let poll = null

const running = computed(() => Boolean(state.value.active))

async function refreshGame() {
  try {
    state.value = await api.getGameState()
  } catch { /* transient */ }
}

async function refreshDisplay() {
  try {
    presentation.value = Boolean((await api.getDisplay()).presentation)
  } catch { /* transient */ }
}

async function setPresentation(enabled) {
  busy.value = true
  try {
    await api.setPresentation(enabled)
    presentation.value = enabled
  } catch { /* transient */ } finally {
    busy.value = false
  }
}

function connect() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${window.location.host}/ws`)
  ws.onmessage = (evt) => {
    let msg
    try { msg = JSON.parse(evt.data) } catch { return }
    if (msg.type === 'game.state') state.value = msg.state
    else if (msg.type === 'display.presentation') presentation.value = Boolean(msg.enabled)
  }
  ws.onclose = () => { ws = null; setTimeout(connect, 2000) }
}

function onGameStarted() {
  tab.value = 'play'
  refreshGame()
}

onMounted(() => {
  refreshGame()
  refreshDisplay()
  connect()
  poll = setInterval(() => { refreshGame(); refreshDisplay() }, 5000)
})
onBeforeUnmount(() => {
  ws?.close()
  if (poll) clearInterval(poll)
})
</script>

<template>
  <div class="phone-app">
    <header class="phone-head">
      <div>
        <p class="eyebrow">AUTODARTS REMOTE</p>
        <h1>{{ running ? state.name : 'No game running' }}</h1>
      </div>
      <button
        class="screen-btn"
        :class="{ on: presentation }"
        :disabled="busy"
        :title="presentation ? 'Restore the main screen' : 'Fill the main screen with the game'"
        @click="setPresentation(!presentation)"
      >{{ presentation ? '⤢ Restore' : '⤢ Fullscreen' }}</button>
    </header>

    <main class="phone-body">
      <template v-if="tab === 'play'">
        <PhoneGameControls v-if="running" :state="state" @updated="state = $event" />
        <div v-else class="empty card">
          <div class="dart">🎯</div>
          <h2>Nothing running yet</h2>
          <p class="muted">Pick something from the Games tab and it'll start on the main screen.</p>
          <button class="primary" @click="tab = 'games'">Choose a game</button>
        </div>
      </template>

      <PhoneGameLibrary v-else-if="tab === 'games'" @started="onGameStarted" />

      <div v-else-if="tab === 'players'">
        <p class="muted intro">
          Names, avatars and selfies sync to the main screen instantly.
        </p>
        <PlayerRoster />
      </div>

      <PhoneLeds v-else-if="tab === 'leds'" />
    </main>

    <nav class="tabbar">
      <button
        v-for="t in TABS"
        :key="t.key"
        :class="{ active: tab === t.key }"
        @click="tab = t.key"
      >
        <span class="icon">{{ t.icon }}</span>
        <span class="label">{{ t.label }}</span>
      </button>
    </nav>
  </div>
</template>

<style scoped>
.phone-app {
  /* room for the fixed tab bar, plus the iOS home indicator */
  padding-bottom: calc(72px + env(safe-area-inset-bottom, 0px));
}

.phone-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
  padding: 0.2rem 0 0.8rem;
  text-align: left;
}

.eyebrow {
  margin: 0 0 0.1rem;
  color: var(--accent);
  font-size: 0.6rem;
  font-weight: 900;
  letter-spacing: 0.16em;
}

.phone-head h1 {
  margin: 0;
  font-size: 1.15rem;
  line-height: 1.2;
}

.screen-btn {
  flex: none;
  padding: 0.55rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
  color: var(--muted);
  font: inherit;
  font-size: 0.75rem;
  font-weight: 700;
  cursor: pointer;
}

.screen-btn.on {
  border-color: var(--accent);
  color: var(--accent);
}

.phone-body {
  min-height: 50vh;
}

.intro {
  margin: 0 0 0.7rem;
  font-size: 0.85rem;
}

.empty {
  text-align: center;
  padding: 1.6rem 1rem;
}

.empty .dart {
  font-size: 2.6rem;
}

.empty h2 {
  margin: 0.4rem 0 0.3rem;
  font-size: 1.1rem;
}

.empty p {
  margin: 0 0 1rem;
  font-size: 0.85rem;
}

.tabbar {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 60;
  /* matches .phone-page-shell so it doesn't stretch across a desktop window */
  width: min(480px, 100%);
  margin: 0 auto;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  padding-bottom: env(safe-area-inset-bottom, 0px);
  border-top: 1px solid var(--border);
  background: rgba(18, 21, 28, 0.97);
  backdrop-filter: blur(12px);
}

.tabbar button {
  min-height: 62px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.15rem;
  border: 0;
  border-top: 2px solid transparent;
  background: transparent;
  color: var(--muted);
  font: inherit;
  cursor: pointer;
}

.tabbar button.active {
  border-top-color: var(--accent);
  color: var(--accent);
}

.tabbar .icon {
  font-size: 1.15rem;
  line-height: 1;
}

.tabbar .label {
  font-size: 0.66rem;
  font-weight: 700;
}
</style>
