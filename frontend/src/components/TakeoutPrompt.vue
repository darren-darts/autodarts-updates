<script setup>
// The board overlay for the two takeout phases, extracted because it appeared
// three times byte-for-byte (Killer, Space Invaders, X01) and needed changing
// in all of them.
//
// The phases are deliberately very different in weight:
//   waiting  - darts are still in the board and nobody can throw. This is a
//              real instruction, so it takes the middle of the screen.
//   detected - detection says they are out and has already advanced the turn.
//              Nothing is required of anyone, so a full-screen banner was just
//              shouting about a thing that went right, and it sat there until
//              the next dart was scored. It shrinks to a corner tab instead,
//              which opens the corrections if it got it wrong.
import { computed, ref, watch } from 'vue'
import TurnCorrections from './TurnCorrections.vue'

const props = defineProps({
  state: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  nextPlayerName: { type: String, default: null },
})
const emit = defineEmits(['confirm', 'undo', 'miss', 'previous', 'override'])

const open = ref(false)

const waiting = computed(() => !props.state.finished && Boolean(props.state.awaiting_takeout))
const detected = computed(
  () => !props.state.finished && !props.state.awaiting_takeout && Boolean(props.state.takeout_override),
)
const current = computed(
  () => (props.state.players ?? []).find((p) => p.player_id === props.state.current_player_id),
)

// Never leave the panel hanging open into a phase it does not belong to.
watch(detected, (is) => { if (!is) open.value = false })
</script>

<template>
  <div v-if="waiting" class="remove-darts">
    <strong>PLEASE REMOVE DARTS</strong>
    <span>Still watching the board{{ nextPlayerName ? ` · ${nextPlayerName} is up next` : '' }}</span>
    <button class="prompt-btn" :disabled="busy" @click="emit('confirm')">Darts removed</button>
  </div>

  <div v-else-if="detected" class="takeout-tab" :class="{ open }">
    <button class="tab-handle" :disabled="busy" @click="open = !open">
      <em>✓</em>
      <span>{{ current?.name ?? 'Next player' }} is up</span>
      <small>{{ open ? 'Close' : 'Wrong? Fix it' }}</small>
    </button>

    <div v-if="open" class="tab-body">
      <TurnCorrections
        :state="state"
        :busy="busy"
        @undo="emit('undo')"
        @miss="emit('miss')"
        @previous="emit('previous')"
        @override="emit('override')"
      />
    </div>
  </div>
</template>

<style scoped>
.remove-darts {
  position: absolute;
  top: 50%;
  left: 50%;
  z-index: 12;
  min-width: min(88%, 420px);
  padding: 20px 26px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  border: 2px solid #ffbf4d;
  border-radius: 14px;
  background: rgba(28, 18, 4, 0.93);
  text-align: center;
  box-shadow: 0 0 60px rgba(255, 191, 77, 0.35), 0 24px 60px rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  transform: translate(-50%, -50%);
  animation: remove-darts-in 0.35s ease-out both;
}

.remove-darts strong {
  color: #ffd89a;
  font-size: clamp(20px, 2.4vw, 32px);
  font-weight: 950;
  letter-spacing: 0.1em;
  line-height: 1.15;
}

.remove-darts span {
  color: #d9c9ad;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.prompt-btn {
  margin-top: 4px;
  min-height: 46px;
  padding: 12px 26px;
  border: 1px solid #d9ff6b;
  border-radius: 10px;
  background: linear-gradient(180deg, #baff19, #6eac08);
  color: #101800;
  font: inherit;
  font-size: 13px;
  font-weight: 900;
  letter-spacing: 0.06em;
  cursor: pointer;
}
.prompt-btn:hover:not(:disabled) { filter: brightness(1.13); }
.prompt-btn:disabled { opacity: 0.38; cursor: default; }

/* Detection got it right: a corner tab, not a banner. */
.takeout-tab {
  position: absolute;
  right: 10px;
  bottom: 10px;
  z-index: 12;
  width: min(94%, 340px);
  border: 1px solid rgba(87, 220, 139, 0.55);
  border-radius: 10px;
  background: rgba(6, 24, 14, 0.94);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
  overflow: hidden;
}
.takeout-tab:not(.open) { width: auto; }

.tab-handle {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  background: transparent;
  color: #9ff0bd;
  font: inherit;
  cursor: pointer;
}
.tab-handle em { font-style: normal; font-size: 12px; }
.tab-handle span { font-size: 11px; font-weight: 800; letter-spacing: 0.06em; }
.tab-handle small {
  margin-left: auto;
  padding: 2px 7px;
  border: 1px solid rgba(87, 220, 139, 0.5);
  border-radius: 999px;
  font-size: 9px;
  font-weight: 900;
  letter-spacing: 0.08em;
}
.tab-handle:disabled { opacity: 0.5; cursor: default; }

.tab-body { padding: 9px 10px 10px; border-top: 1px solid rgba(87, 220, 139, 0.25); }

@keyframes remove-darts-in {
  from { opacity: 0; transform: translate(-50%, -50%) scale(0.9); }
  to { opacity: 1; transform: translate(-50%, -50%) scale(1); }
}

@media (prefers-reduced-motion: reduce) {
  .remove-darts { animation: none; }
}
</style>
