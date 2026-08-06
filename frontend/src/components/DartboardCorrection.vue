<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import DartboardFace from './DartboardFace.vue'

const props = defineProps({
  eventId: { type: Number, required: true },
  detectedLabel: { type: String, default: '' },
  detected: { type: Object, default: null }, // { x_mm, y_mm } - the system's guess, shown for reference
  geometry: { type: Object, required: true },
})
const emit = defineEmits(['saved', 'cancel'])

const BOARD_SIZE = Math.min(560, typeof window !== 'undefined' ? window.innerHeight - 220 : 560)
const LOUPE_SIZE = 220
const LOUPE_RADIUS_MM = 22 // half-width of the zoomed-in view, in board mm

const boardEl = ref(null)
const point = ref(null) // [x_mm, y_mm]
const dragging = ref(false)
const saving = ref(false)
const error = ref(null)
const mouseMm = ref(null)

function clientToMm(clientX, clientY) {
  const svg = boardEl.value?.$el
  if (!svg) return null
  const rect = svg.getBoundingClientRect()
  const vb = svg.viewBox.baseVal
  return [vb.x + ((clientX - rect.left) / rect.width) * vb.width, vb.y + ((clientY - rect.top) / rect.height) * vb.height]
}

function onLayerClick(evt) {
  if (dragging.value) return
  const p = clientToMm(evt.clientX, evt.clientY)
  if (p) point.value = p
}

function startDrag(evt) {
  evt.stopPropagation()
  dragging.value = true
  evt.target.setPointerCapture?.(evt.pointerId)
}

function onLayerPointerMove(evt) {
  const p = clientToMm(evt.clientX, evt.clientY)
  mouseMm.value = p
  if (dragging.value && p) point.value = p
}

function endDrag() {
  dragging.value = false
}

const markers = computed(() => {
  const list = []
  if (props.detected) list.push({ ...props.detected, kind: 'detected' })
  if (point.value) list.push({ x_mm: point.value[0], y_mm: point.value[1], kind: 'correction' })
  return list
})

const loupeCenter = computed(() => mouseMm.value ?? [0, 0])

// Mirrors DartboardFace's own default viewBox math (center [0,0], radius
// physical_board_radius_mm + 8) so the drag handle overlay lines up with
// the marker the SVG itself draws underneath it.
const boardRadius = computed(() => props.geometry.physical_board_radius_mm + 8)
const dragHandleStyle = computed(() => {
  if (!point.value) return {}
  const r = boardRadius.value
  const [x, y] = point.value
  return {
    left: `${((x + r) / (2 * r)) * 100}%`,
    top: `${((y + r) / (2 * r)) * 100}%`,
  }
})

async function save() {
  if (!point.value) return
  saving.value = true
  error.value = null
  try {
    const entry = await api.correctDetectionOnBoard(props.eventId, point.value[0], point.value[1])
    emit('saved', entry)
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

function onKeydown(e) {
  if (e.key === 'Escape') emit('cancel')
}

onMounted(() => {
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="board-correction-overlay">
    <div class="hud-top">
      <p class="hud-instruction">
        Detected as <strong>{{ detectedLabel }}</strong> (pink) — click on the board where the dart
        really landed, then save. The magnifier follows your cursor for precise placement near a wire.
      </p>
      <div class="hud-buttons">
        <button class="ghost" @click="$emit('cancel')">Cancel</button>
        <button class="primary" :disabled="!point || saving" @click="save">
          {{ saving ? 'Saving…' : 'Save correction' }}
        </button>
      </div>
      <p v-if="error" class="status error hud-error">{{ error }}</p>
    </div>

    <div class="board-wrap" :style="{ width: `${BOARD_SIZE}px`, height: `${BOARD_SIZE}px` }">
      <DartboardFace ref="boardEl" :geometry="geometry" :size="BOARD_SIZE" :markers="markers" />
      <div
        class="click-layer"
        @click="onLayerClick"
        @pointermove="onLayerPointerMove"
        @pointerup="endDrag"
        @pointerleave="mouseMm = null"
      >
        <div
          v-if="point"
          class="drag-handle"
          :style="dragHandleStyle"
          @pointerdown="startDrag"
          @pointerup="endDrag"
        ></div>
      </div>
    </div>

    <div class="loupe-panel">
      <DartboardFace
        :geometry="geometry"
        :size="LOUPE_SIZE"
        :center="loupeCenter"
        :view-radius="LOUPE_RADIUS_MM"
        :show-labels="false"
        :markers="markers"
      />
      <p class="loupe-caption">
        {{ mouseMm ? `${mouseMm[0].toFixed(1)}, ${mouseMm[1].toFixed(1)} mm` : 'move mouse over the board' }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.board-correction-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(4, 6, 10, 0.96);
  display: flex;
  align-items: center;
  justify-content: center;
}

.board-wrap {
  position: relative;
}

.click-layer {
  position: absolute;
  inset: 0;
  cursor: crosshair;
  border-radius: 50%;
}

.drag-handle {
  position: absolute;
  width: 20px;
  height: 20px;
  transform: translate(-50%, -50%);
  cursor: grab;
  touch-action: none;
}

.hud-top {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem 1.2rem;
  padding: 0.9rem 1.2rem;
  background: linear-gradient(rgba(0, 0, 0, 0.75), rgba(0, 0, 0, 0));
  pointer-events: none;
}

.hud-top > * {
  pointer-events: auto;
}

.hud-instruction {
  color: #fff;
  font-size: 1rem;
  margin: 0;
  flex: 1 1 auto;
  min-width: 240px;
}

.hud-buttons {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.hud-error {
  flex-basis: 100%;
  margin: 0;
}

.loupe-panel {
  position: fixed;
  right: 1.2rem;
  bottom: 1.2rem;
  background: #0b0f14;
  border: 1px solid #333c4f;
  border-radius: 10px;
  padding: 0.6rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.4rem;
}

.loupe-caption {
  margin: 0;
  font-family: ui-monospace, monospace;
  font-size: 0.75rem;
  color: #96a0b5;
}
</style>
