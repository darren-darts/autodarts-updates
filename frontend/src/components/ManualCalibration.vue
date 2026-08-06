<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const props = defineProps({
  cameraId: { type: Number, required: true },
  initial: { type: Object, default: null },
})
const emit = defineEmits(['saved', 'cancel'])

const ORDER = ['center', '20', '6', '3', '11']
const STEP_LABELS = {
  center: 'Click the middle (bullseye) of the board',
  20: 'Click the outer ring, double 20',
  6: 'Click the outer ring, double 6',
  3: 'Click the outer ring, double 3',
  11: 'Click the outer ring, double 11',
}

const LOUPE_SIZE = 220
const LOUPE_ZOOM = 4

const points = reactive(Object.fromEntries(ORDER.map((k) => [k, null])))
if (props.initial?.points) {
  for (const key of ORDER) {
    const p = props.initial.points[key]
    if (p) points[key] = [p[0], p[1]]
  }
}

const imgEl = ref(null)
const normalized = ref(false)
const loupeCanvas = ref(null)
const dragging = ref(null)
const autoBusy = ref(false)
const saving = ref(false)
const error = ref(null)
const lastMouseNatural = ref(null)

const currentStep = computed(() => ORDER.find((k) => points[k] === null) ?? null)
const allPlaced = computed(() => ORDER.every((k) => points[k] !== null))

function getContentRect() {
  const el = imgEl.value
  if (!el || !el.naturalWidth) return null
  const elRect = el.getBoundingClientRect()
  const scale = Math.min(elRect.width / el.naturalWidth, elRect.height / el.naturalHeight)
  const w = el.naturalWidth * scale
  const h = el.naturalHeight * scale
  return {
    x: elRect.left + (elRect.width - w) / 2,
    y: elRect.top + (elRect.height - h) / 2,
    w,
    h,
    scale,
  }
}

function clientToNatural(clientX, clientY) {
  const r = getContentRect()
  if (!r) return null
  return [(clientX - r.x) / r.scale, (clientY - r.y) / r.scale]
}

function naturalToClient(nx, ny) {
  const r = getContentRect()
  if (!r) return [0, 0]
  return [r.x + nx * r.scale, r.y + ny * r.scale]
}

// Always keep the marker inside the visible image, however it got its
// position (a stale point saved before off-frame detection was fixed, or
// an auto-detect guess landing just past an edge) - a marker you can't see
// is one you can't drag back into place either.
function markerStyle(key) {
  const p = points[key]
  if (!p) return {}
  const r = getContentRect()
  let [x, y] = naturalToClient(p[0], p[1])
  if (r) {
    const margin = 4
    x = Math.min(Math.max(x, r.x + margin), r.x + r.w - margin)
    y = Math.min(Math.max(y, r.y + margin), r.y + r.h - margin)
  }
  return { left: `${x}px`, top: `${y}px` }
}

// One-time correction so an off-frame point that's never dragged still
// saves the clamped (visible, on-the-image) position rather than the
// original off-screen one.
function normalizeOffFramePoints() {
  const el = imgEl.value
  if (!el?.naturalWidth || normalized.value) return
  normalized.value = true
  for (const key of ORDER) {
    const p = points[key]
    if (!p) continue
    const [x, y] = p
    if (x < 0 || y < 0 || x > el.naturalWidth || y > el.naturalHeight) {
      points[key] = [
        Math.min(Math.max(x, 0), el.naturalWidth),
        Math.min(Math.max(y, 0), el.naturalHeight),
      ]
    }
  }
}

function onLayerClick(evt) {
  if (dragging.value) return
  if (!currentStep.value) return
  const n = clientToNatural(evt.clientX, evt.clientY)
  if (n) points[currentStep.value] = n
}

function startDrag(key, evt) {
  evt.stopPropagation()
  dragging.value = key
  evt.target.setPointerCapture?.(evt.pointerId)
}

function onLayerPointerMove(evt) {
  updateMagnifier(evt.clientX, evt.clientY)
  if (dragging.value) {
    const n = clientToNatural(evt.clientX, evt.clientY)
    if (n) points[dragging.value] = n
  }
}

function endDrag() {
  dragging.value = null
}

function updateMagnifier(clientX, clientY) {
  const n = clientToNatural(clientX, clientY)
  const canvas = loupeCanvas.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!n || !imgEl.value?.naturalWidth) {
    ctx.clearRect(0, 0, LOUPE_SIZE, LOUPE_SIZE)
    return
  }
  lastMouseNatural.value = n
  const [nx, ny] = n
  const cropSize = LOUPE_SIZE / LOUPE_ZOOM
  ctx.imageSmoothingEnabled = false
  ctx.clearRect(0, 0, LOUPE_SIZE, LOUPE_SIZE)
  try {
    ctx.drawImage(
      imgEl.value,
      nx - cropSize / 2, ny - cropSize / 2, cropSize, cropSize,
      0, 0, LOUPE_SIZE, LOUPE_SIZE,
    )
  } catch {
    // mid-frame-swap on the MJPEG <img> can occasionally throw; next move retries
  }
  ctx.strokeStyle = '#4fd6e8'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(LOUPE_SIZE / 2, 0)
  ctx.lineTo(LOUPE_SIZE / 2, LOUPE_SIZE)
  ctx.moveTo(0, LOUPE_SIZE / 2)
  ctx.lineTo(LOUPE_SIZE, LOUPE_SIZE / 2)
  ctx.stroke()
}

function clearLoupe() {
  loupeCanvas.value?.getContext('2d').clearRect(0, 0, LOUPE_SIZE, LOUPE_SIZE)
}

function reset() {
  for (const key of ORDER) points[key] = null
  error.value = null
}

async function runAuto() {
  autoBusy.value = true
  error.value = null
  try {
    const grid = await api.autoCalibrate(props.cameraId)
    for (const key of ORDER) {
      const p = grid.points[key]
      points[key] = p ? [p[0], p[1]] : null
    }
  } catch (err) {
    error.value = err.message
  } finally {
    autoBusy.value = false
  }
}

async function save() {
  saving.value = true
  error.value = null
  try {
    const el = imgEl.value
    const payload = {}
    for (const key of ORDER) {
      const [x, y] = points[key]
      // Belt-and-braces: a drag that ends in the letterboxed area around the
      // image (visually clamped to the edge, per markerStyle) could still
      // hold a slightly out-of-bounds natural coordinate - never save one.
      payload[key] = el?.naturalWidth
        ? [Math.min(Math.max(x, 0), el.naturalWidth), Math.min(Math.max(y, 0), el.naturalHeight)]
        : [x, y]
    }
    const grid = await api.saveCalibrationPoints(props.cameraId, payload)
    emit('saved', grid)
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
  <div class="manual-overlay">
    <img
      ref="imgEl"
      class="manual-img"
      :src="`/api/cameras/${cameraId}/stream`"
      alt="camera feed"
      @load="normalizeOffFramePoints"
    />

    <div
      class="click-layer"
      @click="onLayerClick"
      @pointermove="onLayerPointerMove"
      @pointerup="endDrag"
      @pointerleave="clearLoupe"
    >
      <div
        v-for="key in ORDER"
        v-show="points[key]"
        :key="key"
        class="marker"
        :style="markerStyle(key)"
        @pointerdown="startDrag(key, $event)"
        @pointerup="endDrag"
      >
        <span class="marker-dot"></span>
        <span class="marker-label">{{ key === 'center' ? 'C' : key }}</span>
      </div>
    </div>

    <div class="hud-top">
      <p class="hud-instruction">
        {{ currentStep ? STEP_LABELS[currentStep] : 'All 5 points placed — drag any marker to fine-tune, then save.' }}
      </p>
      <div class="hud-buttons">
        <button class="ghost" :disabled="autoBusy" @click="runAuto">
          {{ autoBusy ? 'Detecting…' : 'Auto-detect' }}
        </button>
        <button class="ghost" @click="reset">Reset points</button>
        <button class="ghost" @click="$emit('cancel')">Cancel</button>
        <button class="primary" :disabled="!allPlaced || saving" @click="save">
          {{ saving ? 'Saving…' : 'Save calibration' }}
        </button>
      </div>
      <p v-if="error" class="status error hud-error">{{ error }}</p>
    </div>

    <div class="loupe-panel">
      <canvas ref="loupeCanvas" :width="LOUPE_SIZE" :height="LOUPE_SIZE"></canvas>
      <p class="loupe-caption">
        {{ lastMouseNatural ? `${Math.round(lastMouseNatural[0])}, ${Math.round(lastMouseNatural[1])} px` : 'move mouse over the image' }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.manual-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.manual-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  pointer-events: none;
}

.click-layer {
  position: absolute;
  inset: 0;
  cursor: crosshair;
}

.marker {
  position: absolute;
  z-index: 20;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  cursor: grab;
  touch-action: none;
}

.marker-dot {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: rgba(79, 214, 232, 0.25);
  border: 2px solid #4fd6e8;
  box-shadow: 0 0 0 2px rgba(0, 0, 0, 0.6);
}

.marker-label {
  font-family: ui-monospace, monospace;
  font-size: 0.75rem;
  color: #06202a;
  background: #4fd6e8;
  padding: 0.05rem 0.35rem;
  border-radius: 4px;
  font-weight: 600;
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

.loupe-panel canvas {
  border-radius: 6px;
  background: #000;
  display: block;
}

.loupe-caption {
  margin: 0;
  font-family: ui-monospace, monospace;
  font-size: 0.75rem;
  color: #96a0b5;
}
</style>
