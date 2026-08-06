<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import CalibrationGrid from '../components/CalibrationGrid.vue'
import ManualCalibration from '../components/ManualCalibration.vue'

const slots = ref([]) // [{ deviceId, name }]
const calibrations = reactive({}) // deviceId -> saved grid response | null
const seeds = reactive({}) // deviceId -> { points } from a fresh auto-detect, feeds the modal
const naturalSizes = reactive({}) // deviceId -> { w, h }
const busy = reactive({})
const errors = reactive({})
const manualTarget = ref(null)
const tuneResults = reactive({}) // deviceId -> the last fine-tune report

async function loadSlots() {
  const settings = await api.getSettings()
  slots.value = settings.cameras.slots
    .filter(Boolean)
    .map((s) => ({ deviceId: s.device_id, name: s.name || `Camera ${s.device_id}` }))
  await Promise.all(slots.value.map((s) => refreshCalibration(s.deviceId)))
}

async function refreshCalibration(id) {
  try {
    const data = await api.getCalibration(id)
    calibrations[id] = data.calibrated ? data : null
  } catch (err) {
    errors[id] = err.message
  }
}

// The cameras look at the board from an angle, so auto-detect is only ever
// a starting guess - some landmarks may not even be visible in frame. It
// no longer saves anything by itself; it seeds the manual modal, which is
// where the user confirms/adjusts every point before it's actually saved.
async function runAuto(id) {
  busy[id] = true
  errors[id] = null
  try {
    const data = await api.autoCalibrate(id)
    seeds[id] = { points: data.points, fitStrength: data.fit_strength }
    manualTarget.value = id
  } catch (err) {
    errors[id] = err.message
  } finally {
    busy[id] = false
  }
}

// Measures the board's own red/green double and treble rings and re-solves
// the homography against them, instead of the 5 clicked points. Only saves
// if it can show the fit actually got better - so it's safe to press.
async function runFineTune(id) {
  busy[id] = true
  errors[id] = null
  tuneResults[id] = null
  try {
    const data = await api.fineTuneCalibration(id)
    tuneResults[id] = data
    if (data.applied) await refreshCalibration(id)
  } catch (err) {
    errors[id] = err.message
  } finally {
    busy[id] = false
  }
}

async function runClear(id) {
  errors[id] = null
  tuneResults[id] = null
  try {
    await api.clearCalibration(id)
    calibrations[id] = null
  } catch (err) {
    errors[id] = err.message
  }
}

function onImgLoad(id, evt) {
  naturalSizes[id] = { w: evt.target.naturalWidth, h: evt.target.naturalHeight }
}

function openManual(id) {
  seeds[id] = null // start from the last SAVED calibration, not a stale auto-seed
  manualTarget.value = id
}

function onManualSaved(id, grid) {
  calibrations[id] = grid
  seeds[id] = null
  manualTarget.value = null
}

onMounted(loadSlots)
</script>

<template>
  <div>
    <h1>Calibration</h1>
    <p class="muted">
      Auto-detect gives a rough starting guess — center is reliable, but
      rotation is a best-effort guess (it can't read the printed numbers, so
      it may land on the wrong wedge) — some landmarks may even fall outside
      the frame on angled cameras, which is left for you to place by hand.
      Manual calibration — clicking the middle and the outer ring at doubles
      20, 6, 3 and 11 — is what actually corrects for each camera's lens and
      mounting angle.
    </p>
    <p class="muted">
      Once a camera is calibrated, <strong>Auto fine-tune</strong> measures the
      board's own red and green double and treble rings and re-solves the fit
      against 40 points spread right around the board, instead of the 5 you
      clicked. It only saves if it can show the alignment improved, and it
      reports the before and after error in millimetres.
      <strong>Take the darts out first</strong> — a dart sitting in the board
      hides part of a ring, which is exactly what this is trying to measure.
    </p>

    <p v-if="slots.length === 0" class="muted">
      No cameras assigned yet — set them up on the
      <router-link to="/setup">Setup</router-link> page first.
    </p>

    <div class="camera-grid">
      <div v-for="s in slots" :key="s.deviceId" class="card cal-card">
        <div class="cal-head">
          <h3>{{ s.name }}</h3>
          <span v-if="calibrations[s.deviceId]" class="status ok">
            {{ calibrations[s.deviceId].source === 'fine-tuned' ? 'fine-tuned' : 'manually calibrated' }}
          </span>
          <span v-else class="status muted">not calibrated</span>
        </div>

        <div class="cal-preview">
          <img
            :src="`/api/cameras/${s.deviceId}/stream`"
            :alt="s.name"
            @load="onImgLoad(s.deviceId, $event)"
          />
          <CalibrationGrid
            v-if="naturalSizes[s.deviceId]"
            :grid="calibrations[s.deviceId]"
            :natural-width="naturalSizes[s.deviceId].w"
            :natural-height="naturalSizes[s.deviceId].h"
          />
        </div>

        <p v-if="errors[s.deviceId]" class="status error">{{ errors[s.deviceId] }}</p>

        <!-- what the fine-tune measured, so pressing it isn't a leap of faith -->
        <div v-if="tuneResults[s.deviceId]" class="tune-report" :class="{ applied: tuneResults[s.deviceId].applied }">
          <template v-if="tuneResults[s.deviceId].applied">
            <strong>Fine-tuned ✓</strong>
            <span>
              Average ring error {{ tuneResults[s.deviceId].before_mm }}mm →
              <b>{{ tuneResults[s.deviceId].after_mm }}mm</b>,
              worst {{ tuneResults[s.deviceId].before_max_mm }}mm →
              {{ tuneResults[s.deviceId].after_max_mm }}mm,
              from {{ tuneResults[s.deviceId].samples }} measured ring points
              ({{ tuneResults[s.deviceId].inliers }} agreed).
            </span>
            <span v-if="tuneResults[s.deviceId].worst_segments?.length" class="muted">
              Still least accurate around:
              {{ tuneResults[s.deviceId].worst_segments.map((w) => `${w.segment} (${w.error_mm}mm)`).join(', ') }}
            </span>
          </template>
          <template v-else>
            <strong>Left unchanged</strong>
            <span>{{ tuneResults[s.deviceId].reason }}</span>
          </template>
        </div>

        <div class="actions">
          <button class="primary" :disabled="busy[s.deviceId]" @click="runAuto(s.deviceId)">
            {{ busy[s.deviceId] ? 'Detecting…' : 'Auto-detect & review' }}
          </button>
          <button class="ghost" @click="openManual(s.deviceId)">Manual calibration</button>
          <button
            v-if="calibrations[s.deviceId]"
            class="ghost"
            :disabled="busy[s.deviceId]"
            title="Measures the board's own double and treble rings by colour and snaps the grid onto them. Clear the darts out of the board first."
            @click="runFineTune(s.deviceId)"
          >
            {{ busy[s.deviceId] ? 'Measuring…' : 'Auto fine-tune' }}
          </button>
          <button v-if="calibrations[s.deviceId]" class="ghost" @click="runClear(s.deviceId)">
            Clear
          </button>
        </div>
      </div>
    </div>

    <ManualCalibration
      v-if="manualTarget !== null"
      :camera-id="manualTarget"
      :initial="seeds[manualTarget] ?? calibrations[manualTarget]"
      @saved="(grid) => onManualSaved(manualTarget, grid)"
      @cancel="manualTarget = null"
    />
  </div>
</template>

<style scoped>
.cal-card {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.cal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}

.cal-head h3 {
  margin: 0;
}

.cal-preview {
  position: relative;
  width: 100%;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
}

.cal-preview img {
  width: 100%;
  height: auto;
  display: block;
}

.tune-report {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
  font-size: 0.82rem;
  line-height: 1.45;
}

.tune-report.applied {
  border-color: var(--accent);
}

.tune-report strong {
  font-size: 0.85rem;
}

.tune-report.applied strong {
  color: var(--accent);
}

.tune-report b {
  color: var(--accent);
}
</style>
