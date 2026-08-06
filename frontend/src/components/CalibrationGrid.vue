<script setup>
defineProps({
  grid: { type: Object, default: null },
  naturalWidth: { type: Number, required: true },
  naturalHeight: { type: Number, required: true },
})
</script>

<template>
  <svg
    v-if="grid"
    class="calib-svg"
    :viewBox="`0 0 ${naturalWidth} ${naturalHeight}`"
    preserveAspectRatio="xMidYMid meet"
  >
    <polygon
      v-for="(ring, i) in grid.rings"
      :key="`ring${i}`"
      :points="ring.map((p) => p.join(',')).join(' ')"
      class="ring"
    />
    <line
      v-for="(line, i) in grid.sector_lines"
      :key="`line${i}`"
      :x1="line[0][0]"
      :y1="line[0][1]"
      :x2="line[1][0]"
      :y2="line[1][1]"
      class="sector-line"
    />
    <text
      v-for="label in grid.labels"
      :key="`label${label.segment}`"
      :x="label.x"
      :y="label.y"
      class="seg-label"
    >{{ label.segment }}</text>
    <g v-for="(pt, key) in grid.points" :key="`pt${key}`">
      <line :x1="pt[0] - 16" :y1="pt[1]" :x2="pt[0] + 16" :y2="pt[1]" class="crosshair" />
      <line :x1="pt[0]" :y1="pt[1] - 16" :x2="pt[0]" :y2="pt[1] + 16" class="crosshair" />
    </g>
  </svg>
</template>

<style scoped>
.calib-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.ring {
  fill: none;
  stroke: #4fd6e8;
  stroke-width: 3;
}

.sector-line {
  stroke: #ff3ec8;
  stroke-width: 2.5;
}

.seg-label {
  fill: #4fd6e8;
  stroke: #000;
  stroke-width: 3;
  paint-order: stroke;
  font-size: 26px;
  font-family: ui-monospace, monospace;
  text-anchor: middle;
}

.crosshair {
  stroke: #3b82f6;
  stroke-width: 3;
}
</style>
