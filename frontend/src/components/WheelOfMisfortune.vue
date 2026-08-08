<script setup>
// The end-of-game wheel. The backend has already decided where it lands (the
// result is part of the seeded game script, so an undo cannot re-roll it) -
// this only animates the spin to the segment it was told, then holds there.
import { computed, ref, watch } from 'vue'

const props = defineProps({
  segments: { type: Array, default: () => [] },
  result: { type: Object, default: null },   // { index, label, kind, player } once spun
  spinnerName: { type: String, default: '' },
})

const COLOURS = ['#ff3d8b', '#ff8a1f', '#ffd12e', '#3fc463', '#1fbfc7', '#3f6ee8', '#8b46e0', '#c93ac0']
const SLICE = 360 / 8
const SPINS = 5   // full turns before it settles, so it reads as a spin

const angle = ref(0)

// Landing a segment under the pointer means rotating its centre back to 12
// o'clock, plus whole turns for the theatre of it.
watch(
  () => props.result?.index,
  (index) => {
    if (index == null) return
    angle.value = SPINS * 360 - (index + 0.5) * SLICE
  },
  { immediate: true },
)

const wedges = computed(() =>
  props.segments.slice(0, 8).map((segment, i) => {
    const start = ((i * SLICE - 90) * Math.PI) / 180
    const end = (((i + 1) * SLICE - 90) * Math.PI) / 180
    const point = (a) => `${(100 + 96 * Math.cos(a)).toFixed(2)},${(100 + 96 * Math.sin(a)).toFixed(2)}`
    return {
      ...segment,
      colour: COLOURS[i % COLOURS.length],
      path: `M100,100 L${point(start)} A96,96 0 0 1 ${point(end)} Z`,
      // Text runs outward along the slice's centre line, split so a long
      // label like "Deep clean the bathroom" stays inside its wedge.
      rotation: i * SLICE + SLICE / 2,
      lines: wrap(segment.label),
      won: props.result?.index === i,
    }
  }),
)

function wrap(label, max = 3) {
  const words = String(label).toUpperCase().split(' ')
  const lines = []
  for (const word of words) {
    const last = lines[lines.length - 1]
    if (last && (last + ' ' + word).length <= 12) lines[lines.length - 1] = `${last} ${word}`
    else lines.push(word)
  }
  return lines.slice(0, max)
}
</script>

<template>
  <section class="wheel-panel" :class="{ spun: Boolean(result) }">
    <header>
      <span>WHEEL OF MISFORTUNE</span>
      <small>{{ result ? `${result.player} SPUN IT` : `${spinnerName || 'The loser'} spins at the end` }}</small>
    </header>

    <div class="wheel-stage">
      <i class="wheel-pointer" aria-hidden="true"></i>
      <svg viewBox="0 0 200 200" role="img" :aria-label="result ? `Wheel landed on ${result.label}` : 'Wheel of Misfortune'">
        <g class="wheel-spin" :style="{ transform: `rotate(${angle}deg)` }">
          <path v-for="(wedge, i) in wedges" :key="i" :d="wedge.path" :fill="wedge.colour" class="wedge" />
          <g
            v-for="(wedge, i) in wedges"
            :key="`t${i}`"
            :transform="`rotate(${wedge.rotation} 100 100)`"
          >
            <text
              v-for="(line, l) in wedge.lines"
              :key="l"
              x="100"
              :y="42 + l * 8"
              :class="{ won: wedge.won }"
            >{{ line }}</text>
          </g>
        </g>
        <circle cx="100" cy="100" r="13" class="hub" />
      </svg>
    </div>

    <p v-if="result" class="wheel-result" :class="result.kind">
      <b>{{ result.label }}</b>
      <span>{{ result.kind === 'jackpot' ? 'Chore list wiped clean' : result.kind === 'reward' ? 'A win for the loser' : 'One extra job, sorry' }}</span>
    </p>
    <p v-else class="wheel-idle">Whoever loses the most rounds spins it.</p>
  </section>
</template>

<style scoped>
.wheel-panel { padding: 12px; display: flex; flex-direction: column; gap: 10px; border: 1px solid rgba(120, 160, 220, 0.3); border-radius: 14px; background: linear-gradient(180deg, rgba(16, 26, 48, 0.95), rgba(10, 16, 32, 0.95)); }
.wheel-panel header { display: flex; flex-direction: column; gap: 2px; text-align: center; }
.wheel-panel header span { color: #cfe4ff; font-size: 11px; font-weight: 900; letter-spacing: 0.14em; }
.wheel-panel header small { color: #7f95bb; font-size: 9px; font-weight: 700; letter-spacing: 0.08em; }

.wheel-stage { position: relative; display: grid; place-items: center; }
.wheel-stage svg { width: 100%; max-width: 280px; height: auto; filter: drop-shadow(0 8px 18px rgba(0, 0, 0, 0.5)); }
.wheel-pointer { position: absolute; top: -2px; left: 50%; z-index: 3; width: 0; height: 0; border-top: 18px solid #ff3b30; border-right: 11px solid transparent; border-left: 11px solid transparent; transform: translateX(-50%); filter: drop-shadow(0 2px 2px rgba(0, 0, 0, 0.6)); }

/* 4.4s lands well after the win overlay appears, so the spin is watched
   rather than missed. transform-box keeps the rotation about the SVG centre. */
.wheel-spin { transform-origin: 100px 100px; transition: transform 4.4s cubic-bezier(0.15, 0.85, 0.2, 1); }
.wedge { stroke: rgba(6, 12, 24, 0.85); stroke-width: 1.2; }
.hub { fill: #ffd12e; stroke: #7a3d00; stroke-width: 3; }

text { fill: #10141f; font: 800 7px 'Segoe UI', system-ui, sans-serif; text-anchor: middle; }
text.won { fill: #fff; paint-order: stroke; stroke: rgba(0, 0, 0, 0.55); stroke-width: 2px; }

.wheel-result { margin: 0; padding: 9px 10px; display: flex; flex-direction: column; gap: 2px; border: 1px solid rgba(255, 209, 46, 0.5); border-radius: 10px; background: rgba(255, 209, 46, 0.12); text-align: center; }
.wheel-result b { color: #ffe27a; font-size: 14px; }
.wheel-result span { color: #cbd8ef; font-size: 10px; font-weight: 700; letter-spacing: 0.05em; }
.wheel-result.reward { border-color: rgba(63, 196, 99, 0.55); background: rgba(63, 196, 99, 0.14); }
.wheel-result.reward b { color: #8ef0a8; }
.wheel-result.jackpot { border-color: rgba(56, 217, 241, 0.6); background: rgba(56, 217, 241, 0.14); }
.wheel-result.jackpot b { color: #7ce8fb; }
.wheel-idle { margin: 0; color: #7f95bb; font-size: 10px; font-weight: 700; text-align: center; }

@media (prefers-reduced-motion: reduce) {
  .wheel-spin { transition-duration: 0.001ms; }
}
</style>
