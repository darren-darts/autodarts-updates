<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'

const props = defineProps({
  eventId: { type: Number, required: true },
  detectedLabel: { type: String, default: '' },
  cameras: { type: Array, required: true }, // [{ id, name }] - cameras with a stored evidence frame
})
const emit = defineEmits(['saved', 'cancel'])

const LOUPE_SIZE = 220
const LOUPE_ZOOM = 5

const activeCamera = ref(props.cameras[0]?.id ?? null)
const point = ref(null) // [x, y] in natural (image) px, for activeCamera only
const imgEl = ref(null)
const loupeCanvas = ref(null)
const dragging = ref(false)
const saving = ref(false)
const error = ref(null)
const lastMouseNatural = ref(null)
const imgKey = ref(0) // forces the <img> to remount when switching cameras, so naturalWidth resolves fresh

function switchCamera(id) {
  if (id === activeCamera.value) return
  activeCamera.value = id
  point.value = null // a different camera's image has a different pixel space
  imgKey.value++
}

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

const markerStyle = computed(() => {
  if (!point.value) return {}
  const r = getContentRect()
  let [x, y] = naturalToClient(point.value[0], point.value[1])
  if (r) {
    const margin = 4
    x = Math.min(Math.max(x, r.x + margin), r.x + r.w - margin)
    y = Math.min(Math.max(y, r.y + margin), r.y + r.h - margin)
  }
  return { left: `${x}px`, top: `${y}px` }
})

function onLayerClick(evt) {
  if (dragging.value) return
  const n = clientToNatural(evt.clientX, evt.clientY)
  if (n) point.value = n
}

function startDrag(evt) {
  evt.stopPropagation()
  dragging.value = true
  evt.target.setPointerCapture?.(evt.pointerId)
}

function onLayerPointerMove(evt) {
  updateMagnifier(evt.clientX, evt.clientY)
  if (dragging.value) {
    const n = clientToNatural(evt.clientX, evt.clientY)
    if (n) point.value = n
  }
}

function endDrag() {
  dragging.value = false
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
    // image not fully decoded yet - next move retries
  }
  ctx.strokeStyle = '#ff9d4d'
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

async function save() {
  if (!point.value || activeCamera.value === null) return
  saving.value = true
  error.value = null
  try {
    const entry = await api.correctDetection(props.eventId, activeCamera.value, point.value[0], point.value[1])
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
  <div class="correction-overlay">
    <img
      :key="imgKey"
      ref="imgEl"
      class="correction-img"
      :src="`/api/detection/history/${eventId}/frame/${activeCamera}`"
      alt="dart evidence frame"
    />

    <div
      class="click-layer"
      @click="onLayerClick"
      @pointermove="onLayerPointerMove"
      @pointerup="endDrag"
      @pointerleave="clearLoupe"
    >
      <div v-show="point" class="marker" :style="markerStyle" @pointerdown="startDrag" @pointerup="endDrag">
        <span class="marker-dot"></span>
      </div>
    </div>

    <div class="hud-top">
      <p class="hud-instruction">
        Detected as <strong>{{ detectedLabel }}</strong> — click exactly where the dart really landed on this
        photo, then save. Drag the marker to fine-tune.
      </p>
      <div class="hud-buttons">
        <button
          v-for="cam in cameras"
          :key="cam.id"
          class="ghost"
          :class="{ active: cam.id === activeCamera }"
          @click="switchCamera(cam.id)"
        >
          {{ cam.name }}
        </button>
        <button class="ghost" @click="$emit('cancel')">Cancel</button>
        <button class="primary" :disabled="!point || saving" @click="save">
          {{ saving ? 'Saving…' : 'Save correction' }}
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
.correction-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.correction-img {
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
  cursor: grab;
  touch-action: none;
}

.marker-dot {
  display: block;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: rgba(255, 157, 77, 0.25);
  border: 2px solid #ff9d4d;
  box-shadow: 0 0 0 2px rgba(0, 0, 0, 0.6);
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

.hud-buttons button.active {
  border-color: #ff9d4d;
  color: #ff9d4d;
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
