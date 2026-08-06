<script setup>
// The one place that says what "fix the turn" means, shared by the big screen
// and the phone. Three things can go wrong and each needs its own undo:
//   - the wrong dart was scored        -> Undo dart / Override
//   - a dart was missed entirely       -> Add a miss
//   - the turn changed when it should not have (a takeout fired twice, or
//     early) -> Previous player
// The dart count is shown rather than described, because "correct the number
// of darts thrown" is really "see how many it thinks you threw, then fix it".
import { computed } from 'vue'

const props = defineProps({
  state: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['undo', 'miss', 'previous', 'override'])

const darts = computed(() => props.state.darts_this_turn ?? [])
const perTurn = computed(() => props.state.darts_per_turn ?? 3)
const finished = computed(() => Boolean(props.state.finished))
</script>

<template>
  <div class="fix" :class="{ compact }">
    <div class="fix-count">
      <small>DARTS THIS TURN</small>
      <div class="pips">
        <span v-for="i in perTurn" :key="i" :class="{ on: darts[i - 1] }">
          {{ darts[i - 1]?.label ?? '—' }}
        </span>
      </div>
      <b>{{ darts.length }} of {{ perTurn }}</b>
    </div>

    <div class="fix-row">
      <button class="fixbtn" :disabled="busy || !darts.length" @click="emit('undo')">
        <strong>Remove a dart</strong>
        <span>{{ darts.length ? `Takes off ${darts.at(-1)?.label}` : 'Nothing to remove' }}</span>
      </button>
      <button class="fixbtn" :disabled="busy || finished || darts.length >= perTurn" @click="emit('miss')">
        <strong>Add a miss</strong>
        <span>A dart it never saw</span>
      </button>
      <button v-if="!compact" class="fixbtn" :disabled="busy" @click="emit('override')">
        <strong>Set a dart's score</strong>
        <span>Pick the real number</span>
      </button>
      <button class="fixbtn warn" :disabled="busy" @click="emit('previous')">
        <strong>Previous player</strong>
        <span>Turn changed by mistake</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.fix { display: flex; flex-direction: column; gap: 8px; text-align: left; }

.fix-count { display: flex; align-items: center; gap: 8px; }
.fix-count small { color: var(--muted, #8fa3bd); font-size: 9px; font-weight: 900; letter-spacing: 0.12em; }
.fix-count b { margin-left: auto; font-family: ui-monospace, monospace; font-size: 13px; }

.pips { display: flex; gap: 4px; }
.pips span {
  min-width: 34px;
  padding: 2px 5px;
  border: 1px dashed rgba(140, 165, 195, 0.4);
  border-radius: 5px;
  font-family: ui-monospace, monospace;
  font-size: 10px;
  text-align: center;
}
.pips span.on { border-style: solid; border-color: var(--accent, #38b26e); }

.fix-row { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }

.fixbtn {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 7px 8px;
  border: 1px solid rgba(119, 145, 181, 0.3);
  border-radius: 8px;
  background: rgba(20, 29, 42, 0.9);
  color: #dce3eb;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.fixbtn:hover:not(:disabled) { filter: brightness(1.15); }
.fixbtn:disabled { opacity: 0.35; cursor: default; }
.fixbtn strong { font-size: 11px; font-weight: 800; }
.fixbtn span { color: var(--muted, #8fa3bd); font-size: 9px; }
.fixbtn.warn { border-color: rgba(255, 191, 77, 0.55); background: rgba(255, 191, 77, 0.12); }

/* Phone: bigger targets, one column of full-width rows. */
.fix.compact .fix-row { grid-template-columns: 1fr 1fr; gap: 0.5rem; }
.fix.compact .fixbtn { min-height: 58px; padding: 0.6rem; border-radius: 10px; }
.fix.compact .fixbtn strong { font-size: 0.9rem; }
.fix.compact .fixbtn span { font-size: 0.68rem; }
.fix.compact .fix-count small { font-size: 0.62rem; }
.fix.compact .fix-count b { font-size: 1rem; }
.fix.compact .pips span { min-width: 44px; padding: 0.3rem; font-size: 0.75rem; }
</style>
