<script setup>
import { computed } from 'vue'
import { buildBoardShapes, pointsToPath } from '../dartboardGeometry'

const props = defineProps({
  geometry: { type: Object, required: true }, // { radii_mm, segments, physical_board_radius_mm }
  size: { type: Number, default: 320 },
  // Fluid boards fill their container (arena stages) instead of a fixed px size.
  fluid: { type: Boolean, default: false },
  center: { type: Array, default: () => [0, 0] }, // [x_mm, y_mm] viewBox center - lets a "loupe" pan
  viewRadius: { type: Number, default: null }, // half-width of the viewBox in mm; defaults to the full board
  showLabels: { type: Boolean, default: true },
  markers: { type: Array, default: () => [] }, // [{ x_mm, y_mm, kind, title }]
  theme: { type: String, default: 'classic' }, // classic | killer | space | derby
  highlight: { type: Array, default: () => [] }, // segment numbers to light up (25 = bull)
  // Darts of the current turn, drawn as numbered pins in throw order.
  darts: { type: Array, default: () => [] }, // [{ x_mm, y_mm, label }]
})

const built = computed(() => buildBoardShapes(props.geometry))
const radius = computed(() => props.viewRadius ?? props.geometry.physical_board_radius_mm + 8)
const viewBox = computed(() => {
  const r = radius.value
  const [cx, cy] = props.center
  return `${cx - r} ${cy - r} ${r * 2} ${r * 2}`
})

// Everything inside the SVG is measured in board millimetres, so a fixed
// "11px" font would be 11mm of board - about 4 screen px once the ~470mm
// board is scaled into a small widget, i.e. invisible. Sizing these as a
// fraction of the current view radius instead keeps them legible at both
// the full-board view and the heavily zoomed loupe.
const highlightSet = computed(() => new Set(props.highlight ?? []))
const bullLit = computed(() => highlightSet.value.has(25))

// A dart with no measured position (manual entry) still needs to appear, so
// fall back to the middle of the bed its label names.
function dartPoint(dart) {
  if (dart?.x_mm != null && dart?.y_mm != null) return [dart.x_mm, dart.y_mm]
  const label = dart?.label
  if (!label || label === 'MISS') return null
  if (label === 'BULL') return [0, 0]
  if (label === '25') return [0, -11]
  const m = /^([SDT])(\d+)$/.exec(label)
  if (!m) return null
  const segments = props.geometry.segments
  const index = segments.indexOf(Number(m[2]))
  if (index < 0) return null
  const r = props.geometry.radii_mm
  const radiusMm = m[1] === 'D' ? (r.double_in + r.double_out) / 2
    : m[1] === 'T' ? (r.triple_in + r.triple_out) / 2
      : (r.triple_out + r.double_in) / 2
  const angle = (index * 2 * Math.PI) / segments.length
  return [radiusMm * Math.sin(angle), -radiusMm * Math.cos(angle)]
}

const dartPins = computed(() =>
  (props.darts ?? [])
    .map((d, i) => ({ point: dartPoint(d), index: i + 1, label: d.label, latest: i === props.darts.length - 1 }))
    .filter((d) => d.point),
)

const labelSize = computed(() => radius.value * 0.115)
const markerArm = computed(() => radius.value * 0.05)
const markerDot = computed(() => radius.value * 0.018)
const markerStroke = computed(() => radius.value * 0.009)
</script>

<template>
  <svg class="board-face" :class="[`theme-${theme}`, { fluid }]" :width="size" :height="size" :viewBox="viewBox">
    <circle :cx="0" :cy="0" :r="geometry.physical_board_radius_mm" class="board-surround" />
    <path
      v-for="(shape, i) in built.shapes"
      :key="i"
      :d="pointsToPath(shape.points)"
      :class="[
        'board-shape',
        shape.role,
        { lit: shape.segment === 25 ? bullLit : highlightSet.has(shape.segment) },
      ]"
    />
    <template v-if="showLabels">
      <text
        v-for="label in built.labels"
        :key="label.segment"
        :x="label.pos[0]"
        :y="label.pos[1]"
        :font-size="labelSize"
        text-anchor="middle"
        dominant-baseline="central"
        class="board-label"
        :class="{ lit: highlightSet.has(label.segment) }"
      >{{ label.segment }}</text>
    </template>

    <!-- darts of the current turn, numbered in throw order -->
    <g v-for="pin in dartPins" :key="`pin${pin.index}`" :class="['dart-pin', { latest: pin.latest }]">
      <circle :cx="pin.point[0]" :cy="pin.point[1]" :r="radius * 0.042" />
      <text
        :x="pin.point[0]"
        :y="pin.point[1]"
        :font-size="radius * 0.05"
        text-anchor="middle"
        dominant-baseline="central"
      >{{ pin.index }}</text>
    </g>
    <g
      v-for="(m, i) in markers"
      :key="i"
      :class="['board-marker', m.kind]"
      :stroke-width="markerStroke"
    >
      <title v-if="m.title">{{ m.title }}</title>
      <line :x1="m.x_mm - markerArm" :y1="m.y_mm" :x2="m.x_mm + markerArm" :y2="m.y_mm" />
      <line :x1="m.x_mm" :y1="m.y_mm - markerArm" :x2="m.x_mm" :y2="m.y_mm + markerArm" />
      <circle :cx="m.x_mm" :cy="m.y_mm" :r="markerDot" />
    </g>
  </svg>
</template>

<style scoped>
.board-face {
  display: block;
  background: #0b0f14;
  border-radius: 50%;
}

.board-face.fluid {
  width: 100%;
  height: auto;
}

.board-surround {
  fill: #1c1712;
}

.board-shape {
  stroke: #0b0f14;
  stroke-width: 0.6;
}

.single-outer-a, .single-inner-a {
  fill: #1a1a1a;
}

.single-outer-b, .single-inner-b {
  fill: #e8dcc0;
}

.double-a, .triple-a {
  fill: #2f9e4f;
}

.double-b, .triple-b {
  fill: #c22b2b;
}

.bull-outer {
  fill: #2f9e4f;
}

.bull-inner {
  fill: #c22b2b;
}

/* Themes recolour the board per game. The bed geometry never changes -
   only the palette - so a dart always lands where the real board says. */
.theme-killer .single-outer-a, .theme-killer .single-inner-a { fill: #493550; }
.theme-killer .single-outer-b, .theme-killer .single-inner-b { fill: #74517d; }
.theme-killer .double-a, .theme-killer .triple-a { fill: #67317d; }
.theme-killer .double-b, .theme-killer .triple-b { fill: #b0459d; }
.theme-killer .board-surround { fill: #150c1a; }

.theme-space .single-outer-a, .theme-space .single-inner-a { fill: #12314b; }
.theme-space .single-outer-b, .theme-space .single-inner-b { fill: #d8f7fb; }
.theme-space .double-a, .theme-space .triple-a { fill: #3a7dff; }
.theme-space .double-b, .theme-space .triple-b { fill: #20c9e8; }
.theme-space .board-surround { fill: #03101f; }

.theme-derby .single-outer-a, .theme-derby .single-inner-a { fill: #3d2f1c; }
.theme-derby .single-outer-b, .theme-derby .single-inner-b { fill: #f2e3c2; }
.theme-derby .double-a, .theme-derby .triple-a { fill: #6b8f5e; }
.theme-derby .double-b, .theme-derby .triple-b { fill: #b8892f; }
.theme-derby .board-surround { fill: #241a10; }

/* Fairway green and bunker sand. The lit bed reads as the flag on the green,
   so it is deliberately the only warm colour on the board. */
.theme-golf .single-outer-a, .theme-golf .single-inner-a { fill: #1c4023; }
.theme-golf .single-outer-b, .theme-golf .single-inner-b { fill: #e8dfbc; }
.theme-golf .double-a, .theme-golf .triple-a { fill: #2f7a3c; }
.theme-golf .double-b, .theme-golf .triple-b { fill: #7fbf5a; }
.theme-golf .board-surround { fill: #0b1c0e; }

/* A lit bed is what you're aiming at - it must win over every theme. */
.board-shape.lit {
  fill: #4ade80;
  stroke: #bbf7d0;
  stroke-width: 1.4;
  filter: drop-shadow(0 0 4px rgba(137, 255, 82, 0.85));
}

.theme-space .board-shape.lit { fill: #79edff; stroke: #d9ffff; filter: drop-shadow(0 0 4px rgba(184, 255, 54, 0.8)); }
.theme-killer .board-shape.lit { fill: #63ff3c; stroke: #d9ffd0; filter: drop-shadow(0 0 5px rgba(132, 255, 81, 0.95)); }
.theme-golf .board-shape.lit { fill: #f2c14e; stroke: #fff3cf; filter: drop-shadow(0 0 6px rgba(242, 193, 78, 0.95)); }

.board-label {
  fill: #f2ead6;
  font-family: ui-monospace, monospace;
  font-weight: 700;
  pointer-events: none;
}

.board-label.lit { fill: #9cff55; filter: drop-shadow(0 0 5px #9cff55); }
.theme-space .board-label.lit { fill: #d9ff79; filter: drop-shadow(0 0 5px #b8ff36); }
.theme-killer .board-label.lit { fill: #d6ff62; filter: drop-shadow(0 0 6px #9cff55); }

.dart-pin circle {
  fill: #ffd84f;
  stroke: #11151c;
  stroke-width: 1.5;
}

.dart-pin text {
  fill: #11151c;
  font-family: ui-monospace, monospace;
  font-weight: 700;
}

.dart-pin.latest circle {
  fill: #fff;
  stroke: #ffd84f;
  stroke-width: 2.5;
}

/* Sizes (font-size, stroke-width, marker radius) are bound as attributes
   from the script - they scale with the view so they stay legible whether
   the whole board or a 22mm crop is on screen. */
.board-marker {
  pointer-events: none;
  stroke-linecap: round;
}

.board-marker circle {
  fill-opacity: 0.25;
}

.board-marker.detected line,
.board-marker.detected circle {
  stroke: #ff3ec8;
  fill: #ff3ec8;
}

.board-marker.correction line,
.board-marker.correction circle {
  stroke: #ff9d4d;
  fill: #ff9d4d;
}
</style>
