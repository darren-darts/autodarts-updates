<script setup>
// Big, zoomable, pannable dartboard for picking a bed with a finger.
//
// A mis-detected dart is usually one bed away from the truth, so the two beds
// you're choosing between are adjacent and small. At full-board zoom a treble
// bed is a few millimetres of screen and a fingertip covers several of them,
// so this supports pinch-zoom and drag-pan, and confirms the choice in text
// before it's applied - you should never have to trust that you hit the right
// wedge.
//
// Selection is by tap, not by drag-release, and a tap only counts if the
// finger barely moved; otherwise every pan would also re-select a bed.
import { computed, ref } from 'vue'

const props = defineProps({
  target: { type: String, default: 'MISS' },
})
const emit = defineEmits(['pick'])

const SEGMENTS = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]
const FULL_VIEW = 215        // half-width of the un-zoomed viewBox, in board mm
const MIN_ZOOM = 1
const MAX_ZOOM = 6
const TAP_SLOP_PX = 12       // finger movement still counted as a tap, not a pan

const zoom = ref(1)
const panX = ref(0)          // board-mm offset of the view centre
const panY = ref(0)
const svgEl = ref(null)
const marker = ref(null)     // [x_mm, y_mm] where the finger landed

// Pointer bookkeeping for pan + pinch.
// `pinched` latches for the whole gesture: without it, lifting the second
// finger of a pinch looks exactly like a one-finger tap (size 1, no movement
// since the last move event) and would silently re-select whatever bed sat
// under that finger.
const pointers = new Map()
let startDistance = 0
let startZoom = 1
let panStart = null
let moved = 0
let pinched = false

const half = computed(() => FULL_VIEW / zoom.value)
const viewBox = computed(() => {
  const h = half.value
  return `${panX.value - h} ${panY.value - h} ${h * 2} ${h * 2}`
})

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
    for (const [kind, inner, outer, colour] of [
      ['S', 107, 162, single],
      ['D', 162, 170, ring],
      ['S', 15.9, 99, single],
      ['T', 99, 107, ring],
    ]) {
      out.push({ target: `${kind}${number}`, d: ringPath(inner, outer, start, end), colour })
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

// Everything inside the SVG is in board millimetres, so text and strokes must
// scale with the zoom or they'd become unreadably large when zoomed in.
const labelSize = computed(() => half.value * 0.075)
const markerSize = computed(() => half.value * 0.03)
const strokeWidth = computed(() => half.value * 0.004)

const score = computed(() => {
  const t = props.target
  if (t === 'MISS') return 0
  if (t === '25') return 25
  if (t === 'BULL') return 50
  const m = /^([SDT])(\d+)$/.exec(t)
  return m ? Number(m[2]) * { S: 1, D: 2, T: 3 }[m[1]] : 0
})

const description = computed(() => {
  const t = props.target
  if (t === 'MISS') return 'Miss / outside the board'
  if (t === '25') return 'Outer bull'
  if (t === 'BULL') return 'Bullseye'
  const m = /^([SDT])(\d+)$/.exec(t)
  if (!m) return t
  return `${{ S: 'Single', D: 'Double', T: 'Treble' }[m[1]]} ${m[2]}`
})

function clientToBoard(clientX, clientY) {
  const svg = svgEl.value
  if (!svg) return null
  const rect = svg.getBoundingClientRect()
  const h = half.value
  return [
    panX.value - h + ((clientX - rect.left) / rect.width) * h * 2,
    panY.value - h + ((clientY - rect.top) / rect.height) * h * 2,
  ]
}

/** Which bed contains this board-space point - computed from geometry rather
 *  than hit-testing the SVG, so a tap between two paths still resolves. */
function bedAt(x, y) {
  const radius = Math.hypot(x, y)
  if (radius <= 6.35) return 'BULL'
  if (radius <= 15.9) return '25'
  if (radius > 170) return 'MISS'
  // 0 = top, increasing clockwise, matching the board model
  let angle = (Math.atan2(x, -y) * 180) / Math.PI
  if (angle < 0) angle += 360
  const index = Math.floor((angle + 9) / 18) % 20
  const number = SEGMENTS[index]
  if (radius >= 162) return `D${number}`
  if (radius >= 99 && radius <= 107) return `T${number}`
  return `S${number}`
}

function clampPan() {
  const limit = Math.max(0, FULL_VIEW - half.value)
  panX.value = Math.max(-limit, Math.min(limit, panX.value))
  panY.value = Math.max(-limit, Math.min(limit, panY.value))
}

function setZoom(next, focus = null) {
  const clamped = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, next))
  if (focus) {
    // Keep the focal board-point under the same finger position.
    const before = half.value
    const after = FULL_VIEW / clamped
    const k = 1 - after / before
    panX.value += (focus[0] - panX.value) * k
    panY.value += (focus[1] - panY.value) * k
  }
  zoom.value = clamped
  clampPan()
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

function beginPan(x, y) {
  panStart = { x, y, panX: panX.value, panY: panY.value }
}

function onPointerDown(evt) {
  svgEl.value?.setPointerCapture?.(evt.pointerId)
  pointers.set(evt.pointerId, { x: evt.clientX, y: evt.clientY })
  moved = 0
  if (pointers.size === 1) {
    pinched = false
    beginPan(evt.clientX, evt.clientY)
  } else if (pointers.size === 2) {
    const [a, b] = [...pointers.values()]
    startDistance = distance(a, b)
    startZoom = zoom.value
    pinched = true
    panStart = null
  }
}

function onPointerMove(evt) {
  if (!pointers.has(evt.pointerId)) return
  const previous = pointers.get(evt.pointerId)
  moved += Math.hypot(evt.clientX - previous.x, evt.clientY - previous.y)
  pointers.set(evt.pointerId, { x: evt.clientX, y: evt.clientY })

  if (pointers.size === 2 && startDistance > 0) {
    const [a, b] = [...pointers.values()]
    const mid = clientToBoard((a.x + b.x) / 2, (a.y + b.y) / 2)
    setZoom(startZoom * (distance(a, b) / startDistance), mid)
    return
  }

  if (pointers.size === 1 && panStart) {
    const rect = svgEl.value.getBoundingClientRect()
    const perPx = (half.value * 2) / rect.width
    panX.value = panStart.panX - (evt.clientX - panStart.x) * perPx
    panY.value = panStart.panY - (evt.clientY - panStart.y) * perPx
    clampPan()
  }
}

function onPointerUp(evt) {
  const wasSingle = pointers.size === 1
  pointers.delete(evt.pointerId)
  if (pointers.size < 2) startDistance = 0

  // A tap: one finger, barely moved, and not the tail of a pinch.
  if (wasSingle && !pinched && moved <= TAP_SLOP_PX) {
    const board = clientToBoard(evt.clientX, evt.clientY)
    if (board) {
      marker.value = board
      emit('pick', bedAt(board[0], board[1]))
    }
  }

  if (pointers.size === 0) {
    panStart = null
    pinched = false
  } else {
    // A finger is still down after a pinch - hand panning back to it rather
    // than making the user lift and re-touch to drag again.
    const [remaining] = [...pointers.values()]
    beginPan(remaining.x, remaining.y)
  }
  moved = 0
}

function onWheel(evt) {
  evt.preventDefault()
  const focus = clientToBoard(evt.clientX, evt.clientY)
  setZoom(zoom.value * (evt.deltaY < 0 ? 1.15 : 1 / 1.15), focus)
}

function reset() {
  zoom.value = 1
  panX.value = 0
  panY.value = 0
}

function nudgeZoom(factor) {
  setZoom(zoom.value * factor, [panX.value, panY.value])
}

function chooseMiss() {
  marker.value = null
  emit('pick', 'MISS')
}
</script>

<template>
  <div class="phone-picker">
    <div class="readout" :class="{ miss: target === 'MISS' }">
      <div>
        <small>SELECTED</small>
        <strong>{{ target }}</strong>
      </div>
      <div class="readout-detail">
        <span>{{ description }}</span>
        <b>{{ score }} points</b>
      </div>
    </div>

    <div class="board-wrap">
      <svg
        ref="svgEl"
        :viewBox="viewBox"
        aria-label="Zoomable dartboard - tap a bed to select it"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
        @wheel="onWheel"
      >
        <circle :r="207" fill="#080b0f" stroke="#536071" :stroke-width="strokeWidth * 2" />
        <path
          v-for="zone in zones"
          :key="zone.target + zone.d"
          class="zone"
          :class="{ active: zone.target === target }"
          :d="zone.d"
          :fill="zone.colour"
          :stroke-width="strokeWidth"
        />
        <circle
          class="zone"
          :class="{ active: target === '25' }"
          :r="15.9" fill="#248a56" :stroke-width="strokeWidth"
        />
        <circle
          class="zone"
          :class="{ active: target === 'BULL' }"
          :r="6.35" fill="#c83247" :stroke-width="strokeWidth"
        />
        <text
          v-for="label in labels"
          :key="label.number"
          class="num"
          :x="label.x"
          :y="label.y"
          :font-size="labelSize"
        >{{ label.number }}</text>

        <!-- where the finger actually landed, so a near-miss tap is obvious -->
        <g v-if="marker" class="marker" :stroke-width="strokeWidth * 2">
          <circle :cx="marker[0]" :cy="marker[1]" :r="markerSize" />
          <line :x1="marker[0] - markerSize * 2.2" :y1="marker[1]" :x2="marker[0] + markerSize * 2.2" :y2="marker[1]" />
          <line :x1="marker[0]" :y1="marker[1] - markerSize * 2.2" :x2="marker[0]" :y2="marker[1] + markerSize * 2.2" />
        </g>
      </svg>

      <div class="zoom-controls">
        <button type="button" aria-label="Zoom in" @click="nudgeZoom(1.5)">＋</button>
        <button type="button" aria-label="Zoom out" @click="nudgeZoom(1 / 1.5)">－</button>
        <button type="button" class="reset" aria-label="Reset zoom" @click="reset">⟲</button>
      </div>
      <span class="zoom-badge">{{ zoom.toFixed(1) }}×</span>
    </div>

    <p class="hint">Pinch or use ＋ to zoom, drag to move, tap a bed to select it.</p>
    <button class="miss-btn" :class="{ active: target === 'MISS' }" type="button" @click="chooseMiss">
      Miss / outside board · 0
    </button>
  </div>
</template>

<style scoped>
.phone-picker {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.readout {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.6rem 0.9rem;
  border: 1px solid var(--accent);
  border-radius: 10px;
  background: rgba(56, 178, 110, 0.1);
}

.readout.miss {
  border-color: #e05555;
  background: rgba(224, 85, 85, 0.1);
}

.readout small {
  display: block;
  color: var(--muted);
  font-size: 0.62rem;
  font-weight: 900;
  letter-spacing: 0.14em;
}

.readout strong {
  font-size: 1.9rem;
  line-height: 1.1;
}

.readout-detail {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.1rem;
}

.readout-detail span {
  color: var(--muted);
  font-size: 0.78rem;
}

.readout-detail b {
  font-size: 0.95rem;
}

.board-wrap {
  position: relative;
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  background: radial-gradient(circle at 50% 46%, #1a2230, #080d13 70%);
}

svg {
  width: 100%;
  display: block;
  /* the element owns all gestures - the page must not scroll or pinch-zoom
     underneath the board while a bed is being chosen */
  touch-action: none;
  cursor: crosshair;
}

.zone {
  stroke: #222932;
}

.zone.active {
  stroke: #ffd84f;
  stroke-width: 3;
}

.num {
  fill: #f5f6f8;
  font-weight: 900;
  text-anchor: middle;
  dominant-baseline: middle;
  pointer-events: none;
}

.marker {
  pointer-events: none;
  stroke: #ffd84f;
  stroke-linecap: round;
}

.marker circle {
  fill: rgba(255, 216, 79, 0.25);
}

.zoom-controls {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.zoom-controls button {
  width: 44px;
  height: 44px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: rgba(8, 13, 19, 0.85);
  color: var(--text);
  font-size: 1.2rem;
  font-weight: 700;
  cursor: pointer;
}

.zoom-controls .reset {
  font-size: 1rem;
}

.zoom-badge {
  position: absolute;
  left: 8px;
  bottom: 8px;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: rgba(8, 13, 19, 0.85);
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 700;
}

.hint {
  margin: 0;
  color: var(--muted);
  font-size: 0.76rem;
  text-align: center;
}

.miss-btn {
  width: 100%;
  padding: 0.85rem;
  border: 1px solid rgba(224, 85, 85, 0.4);
  border-radius: 10px;
  background: rgba(224, 85, 85, 0.1);
  color: #ffc0c4;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.miss-btn.active {
  border-color: #e05555;
  background: rgba(224, 85, 85, 0.24);
  color: #fff;
}
</style>
