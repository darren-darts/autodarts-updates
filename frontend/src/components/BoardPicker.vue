<script setup>
// Clickable virtual dartboard: tap a bed to pick S/D/T/25/BULL. Emits the
// chosen target label. Geometry uses the standard board radii in mm, same
// convention as the backend's board model (y down, 20 at the top).
import { computed } from 'vue'

const props = defineProps({
  target: { type: String, default: 'MISS' },
})
const emit = defineEmits(['pick'])

const SEGMENTS = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]

function point(radius, angleDeg) {
  const a = (angleDeg * Math.PI) / 180
  return [Math.sin(a) * radius, -Math.cos(a) * radius]
}

function ringPath(inner, outer, start, end) {
  const [ax, ay] = point(inner, start)
  const [bx, by] = point(outer, start)
  const [cx, cy] = point(outer, end)
  const [dx, dy] = point(inner, end)
  return `M ${ax} ${ay} L ${bx} ${by} A ${outer} ${outer} 0 0 1 ${cx} ${cy} L ${dx} ${dy} A ${inner} ${inner} 0 0 0 ${ax} ${ay} Z`
}

const zones = computed(() => {
  const out = []
  SEGMENTS.forEach((number, index) => {
    const start = index * 18 - 9
    const end = index * 18 + 9
    const single = index % 2 ? '#e4dfd1' : '#151c20'
    const ring = index % 2 ? '#248a56' : '#c83247'
    for (const [kind, inner, outer, color] of [
      ['S', 107, 162, single],
      ['D', 162, 170, ring],
      ['S', 15.9, 99, single],
      ['T', 99, 107, ring],
    ]) {
      out.push({ target: `${kind}${number}`, d: ringPath(inner, outer, start, end), color })
    }
  })
  return out
})

const labels = computed(() =>
  SEGMENTS.map((number, index) => {
    const [x, y] = point(190, index * 18)
    return { number, x, y }
  }),
)
</script>

<template>
  <div class="board-picker">
    <svg viewBox="-215 -215 430 430" aria-label="Clickable virtual dartboard">
      <circle r="207" fill="#080b0f" stroke="#536071" stroke-width="3" />
      <path
        v-for="zone in zones"
        :key="zone.target + zone.d"
        class="zone"
        :class="{ active: zone.target === target }"
        :d="zone.d"
        :fill="zone.color"
        @click="emit('pick', zone.target)"
      />
      <circle class="zone" :class="{ active: target === '25' }" r="15.9" fill="#248a56" @click="emit('pick', '25')" />
      <circle class="zone" :class="{ active: target === 'BULL' }" r="6.35" fill="#c83247" @click="emit('pick', 'BULL')" />
      <text
        v-for="label in labels"
        :key="label.number"
        class="num"
        :x="label.x"
        :y="label.y"
      >{{ label.number }}</text>
    </svg>
    <button class="miss" :class="{ active: target === 'MISS' }" type="button" @click="emit('pick', 'MISS')">
      Miss / outside board · 0
    </button>
  </div>
</template>

<style scoped>
.board-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

svg {
  width: 100%;
  display: block;
  filter: drop-shadow(0 15px 22px rgba(0, 0, 0, 0.55));
  cursor: crosshair;
}

.zone {
  stroke: #222932;
  stroke-width: 0.8;
}

.zone:hover {
  filter: brightness(1.38);
}

.zone.active {
  stroke: #ffd84f;
  stroke-width: 4;
}

.num {
  fill: #f5f6f8;
  font-size: 13px;
  font-weight: 900;
  text-anchor: middle;
  dominant-baseline: middle;
  pointer-events: none;
}

.miss {
  width: 100%;
  padding: 10px;
  border: 1px solid rgba(255, 95, 105, 0.35);
  border-radius: 8px;
  background: rgba(255, 95, 105, 0.08);
  color: #ffc0c4;
  font: inherit;
  font-size: 0.8rem;
  font-weight: 700;
  cursor: pointer;
}

.miss.active {
  border-color: #ff5f69;
  background: rgba(255, 95, 105, 0.2);
  color: white;
}
</style>
