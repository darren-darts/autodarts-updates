<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import DartCorrection from '../components/DartCorrection.vue'
import DartboardCorrection from '../components/DartboardCorrection.vue'
import DartboardFace from '../components/DartboardFace.vue'
import { playHitSound, playReviewSound, playTakeoutSound, unlockAudio } from '../sound'
import CalibrationGrid from '../components/CalibrationGrid.vue'

// Fusion needs 2+ cameras to agree, so this is one shared session across all
// configured+calibrated cameras, not a per-camera on/off switch.
const slots = ref([]) // [{ deviceId, name }] - all configured cameras
const calibratedIds = ref(new Set())
const naturalSizes = reactive({}) // deviceId -> { w, h }

const sessionActive = ref(false)
const sessionState = ref('stopped')
const sessionMessage = ref('')
const busy = ref(false)
const error = ref(null)

const latestHit = ref(null) // last FusedHit, kept on screen until the next one
const candidateLines = reactive({}) // deviceId -> { line: [x1,y1,x2,y2], confidence }
const log = ref([]) // [{ text, kind, ts }] newest first
// Takeout is the one event with nothing to show for it - no dart, no marker -
// so when it misfires or doesn't fire there is nothing to look at. These make
// it visible: every takeout, why it fired, and whether the game was waiting.
const takeouts = ref([]) // [{ id, camera_ids, reason, awaiting, occupancy, ts }]
const awaitingTakeout = ref(false)
const takeoutFlash = ref(false)
let takeoutFlashTimer = null
const history = ref([]) // raw FusedHit dicts from the backend, newest first

// Sound is opt-out and remembered, since whether it's wanted depends on the
// room, not the session.
const soundEnabled = ref(localStorage.getItem('autodarts.sound') !== 'off')
const expandedCamera = ref(null) // deviceId shown alone at full width, or null for the normal grid
const cameraHealth = ref({}) // deviceId -> { delivering, frames, error } while a session is running
// Board grid drawn over each live feed: the fastest way to see whether the
// calibration actually lines up with the physical wires, which is what
// decides borderline segment calls near the bull.
const showGrid = ref(localStorage.getItem('autodarts.grid') !== 'off')
const grids = reactive({}) // deviceId -> saved calibration grid, projected into that camera's image
const hitPixels = ref({}) // deviceId -> [x, y] where the latest fused hit lands in that image
const correctingEvent = ref(null) // { eventId, detectedLabel, cameras } while the photo correction overlay is open
const boardCorrecting = ref(null) // { eventId, detectedLabel, detected } while the board-diagram overlay is open
const geometry = ref(null) // { radii_mm, segments, physical_board_radius_mm } for DartboardFace
const analysis = ref(null)
const analyzing = ref(false)
const analyzeError = ref(null)

let ws = null
let statusTimer = null

const calibratedCount = computed(() => slots.value.filter((s) => calibratedIds.value.has(s.deviceId)).length)

const latestHitMarkers = computed(() => {
  if (!latestHit.value || latestHit.value.x_mm === null) return []
  const list = [{
    x_mm: latestHit.value.x_mm,
    y_mm: latestHit.value.y_mm,
    kind: 'detected',
    title: `Detected ${latestHit.value.label}`,
  }]
  if (latestHit.value.correction) {
    list.push({
      x_mm: latestHit.value.correction.x_mm,
      y_mm: latestHit.value.correction.y_mm,
      kind: 'correction',
      title: `You marked ${latestHit.value.correction.label}`,
    })
  }
  return list
})

function cameraName(id) {
  return slots.value.find((s) => s.deviceId === id)?.name ?? `Camera ${id}`
}

function toggleExpanded(deviceId) {
  expandedCamera.value = expandedCamera.value === deviceId ? null : deviceId
}

function toggleSound() {
  soundEnabled.value = !soundEnabled.value
  localStorage.setItem('autodarts.sound', soundEnabled.value ? 'on' : 'off')
  // Doubles as the user gesture the browser needs before it will let us
  // play anything, and as an audible confirmation of the new setting.
  if (soundEnabled.value) {
    unlockAudio()
    playHitSound()
  }
}

function openCorrection(entry) {
  const cameraIds = entry.evidence_cameras?.length ? entry.evidence_cameras : entry.cameras_used
  if (!cameraIds?.length) return
  correctingEvent.value = {
    eventId: entry.event_id,
    detectedLabel: entry.label,
    cameras: cameraIds.map((id) => ({ id, name: cameraName(id) })),
  }
}

function openBoardCorrection(entry) {
  if (entry.event_id === undefined || entry.event_id === null) {
    error.value = 'That throw predates this detection session, so it can no longer be corrected.'
    return
  }
  boardCorrecting.value = {
    eventId: entry.event_id,
    detectedLabel: entry.label,
    detected: entry.x_mm !== null ? { x_mm: entry.x_mm, y_mm: entry.y_mm } : null,
  }
}

// Shared by both correction flows - the backend returns the same updated
// history-entry shape either way, so there's one merge path regardless of
// which overlay produced it.
function applyCorrectedEntry(updatedEntry) {
  const idx = history.value.findIndex((h) => h.event_id === updatedEntry.event_id)
  if (idx !== -1) history.value[idx] = updatedEntry
  if (latestHit.value?.event_id === updatedEntry.event_id) latestHit.value = updatedEntry
  correctingEvent.value = null
  boardCorrecting.value = null
}

async function loadGeometry() {
  try {
    geometry.value = await api.getBoardGeometry()
  } catch {
    // the board diagram is a nice-to-have - detection itself still works without it
  }
}

async function runAnalyze() {
  analyzing.value = true
  analyzeError.value = null
  try {
    analysis.value = await api.analyzeDetection()
  } catch (err) {
    analyzeError.value = err.message
  } finally {
    analyzing.value = false
  }
}

// accepted doesn't mean unambiguous: a hit can pass the geometry/confidence
// gates and still land close enough to a wire that the segment call itself
// is uncertain (fusion.py's score_uncertain). Worth flagging even when
// accepted, since a confident-looking wrong segment is exactly what this
// silently hides otherwise.
function nearWire(hit) {
  return !!hit && hit.accepted && hit.score_uncertain
}

function describeHit(hit) {
  if (!hit) return ''
  const status = hit.accepted
    ? nearWire(hit)
      ? ' (near a wire — verify)'
      : ''
    : hit.review_required
      ? ' (needs confirmation)'
      : ' (rejected)'
  return `${hit.label} — ${hit.score} pts, ${Math.round(hit.confidence * 100)}% confidence${status}`
}

function pushLog(text, kind) {
  log.value = [{ text, kind, ts: Date.now() }, ...log.value].slice(0, 25)
}

function connectWs() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${window.location.host}/ws`)
  ws.onmessage = (evt) => {
    let msg
    try {
      msg = JSON.parse(evt.data)
    } catch {
      return
    }
    if (msg.type === 'detection.hit') {
      latestHit.value = msg.hit
      projectHit(msg.hit)
      history.value = [msg.hit, ...history.value].slice(0, 40)
      pushLog(describeHit(msg.hit), nearWire(msg.hit) ? 'near-wire' : msg.hit.accepted ? 'hit' : 'review')
      // Same condition the backend uses to fire the green LED flash
      // (session.py: hit.x_mm is not None), so light and sound always agree.
      if (soundEnabled.value && msg.hit.x_mm !== null) {
        if (msg.hit.accepted && !msg.hit.score_uncertain) playHitSound()
        else playReviewSound()
      }
    } else if (msg.type === 'detection.takeout') {
      for (const key of Object.keys(candidateLines)) delete candidateLines[key]
      pushLog(`DARTS REMOVED — ${msg.reason ?? 'takeout'} (cameras ${(msg.camera_ids ?? []).join(', ')})`, 'takeout')
      takeouts.value = [{ ...msg, ts: (msg.ts ?? Date.now() / 1000) * 1000 }, ...takeouts.value].slice(0, 25)
      takeoutFlash.value = true
      clearTimeout(takeoutFlashTimer)
      takeoutFlashTimer = setTimeout(() => { takeoutFlash.value = false }, 1500)
      if (soundEnabled.value) playTakeoutSound()
    }
  }
  ws.onclose = () => {
    ws = null
    setTimeout(connectWs, 2000)
  }
}

async function refreshStatus() {
  try {
    const status = await api.getDetectionStatus()
    sessionActive.value = status.active
    sessionState.value = status.state
    sessionMessage.value = status.message
    if (status.latest_hit) {
      const changed = status.latest_hit.event_id !== latestHit.value?.event_id
      latestHit.value = status.latest_hit
      if (changed) projectHit(status.latest_hit)
    }
    if (status.history) history.value = status.history
    cameraHealth.value = status.camera_health || {}
    awaitingTakeout.value = Boolean(status.game_awaiting_takeout)
    if (status.takeouts) {
      takeouts.value = status.takeouts.map((t) => ({ ...t, ts: t.ts * 1000 }))
    }
    for (const key of Object.keys(candidateLines)) delete candidateLines[key]
    for (const [idStr, candidate] of Object.entries(status.latest_candidates || {})) {
      candidateLines[Number(idStr)] = { line: candidate.image_line, confidence: candidate.confidence }
    }
  } catch {
    // transient - next poll will retry
  }
}

async function loadSlots() {
  const settings = await api.getSettings()
  slots.value = settings.cameras.slots
    .filter(Boolean)
    .map((s) => ({ deviceId: s.device_id, name: s.name || `Camera ${s.device_id}` }))
  const calibrations = await api.getAllCalibrations()
  calibratedIds.value = new Set(Object.keys(calibrations.cameras).map(Number))
  for (const id of calibratedIds.value) {
    try {
      const g = await api.getCalibration(id)
      if (g.calibrated) grids[id] = g
    } catch { /* a camera without a usable grid just shows no overlay */ }
  }
}

function toggleGrid() {
  showGrid.value = !showGrid.value
  localStorage.setItem('autodarts.grid', showGrid.value ? 'on' : 'off')
}

// Where the fused board position lands in each camera image, so the call can
// be checked against the physical wires in every view at once.
async function projectHit(hit) {
  if (!hit || hit.x_mm === null || hit.x_mm === undefined) {
    hitPixels.value = {}
    return
  }
  try {
    const res = await api.projectBoardPoint(hit.x_mm, hit.y_mm)
    hitPixels.value = res.points
  } catch {
    hitPixels.value = {}
  }
}

async function start() {
  busy.value = true
  error.value = null
  // This click is the user gesture browsers require before audio may play;
  // without it the first dart's sound would be silently blocked.
  if (soundEnabled.value) unlockAudio()
  try {
    await api.startDetection()
    await refreshStatus()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function stop() {
  busy.value = true
  try {
    await api.stopDetection()
    sessionActive.value = false
    sessionState.value = 'stopped'
    for (const key of Object.keys(candidateLines)) delete candidateLines[key]
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

function onImgLoad(id, evt) {
  naturalSizes[id] = { w: evt.target.naturalWidth, h: evt.target.naturalHeight }
}

// Escape collapses an enlarged camera, matching the correction overlays.
// Skipped while one of those is open so it doesn't steal their Escape.
function onKeydown(e) {
  if (e.key !== 'Escape') return
  if (correctingEvent.value || boardCorrecting.value) return
  expandedCamera.value = null
}

onMounted(() => {
  loadSlots()
  loadGeometry()
  refreshStatus()
  connectWs()
  statusTimer = setInterval(refreshStatus, 1000)
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  ws?.close()
  if (statusTimer) clearInterval(statusTimer)
  clearTimeout(takeoutFlashTimer)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div>
    <h1>Dart detection</h1>
    <p class="muted">
      One session watches every calibrated camera together — each fits its
      own view of the dart as a line, and the score comes from where 2 or 3
      cameras' lines cross, not from any single camera's guess. Start the
      session, then throw a real dart at the board.
    </p>

    <div class="card session-card" :class="{ 'takeout-flash': takeoutFlash }">
      <div class="session-row">
        <div>
          <span class="status" :class="sessionActive ? 'ok' : 'muted'">{{ sessionState }}</span>
          <span v-if="awaitingTakeout" class="awaiting-chip">GAME IS WAITING FOR A TAKEOUT</span>
          <span v-if="sessionState === 'clearing'" class="clearing-chip">BOARD CLEARING</span>
          <p class="muted session-message">{{ sessionMessage || 'Not running' }}</p>
        </div>
        <div class="actions">
          <button
            class="ghost"
            title="Overlay the saved calibration grid on each camera — if the rings and wedge lines don't sit on the real wires, that camera needs recalibrating"
            @click="toggleGrid"
          >
            {{ showGrid ? '▦ Grid on' : '▢ Grid off' }}
          </button>
          <button
            class="ghost"
            :title="soundEnabled ? 'Sound on — click to mute' : 'Sound off — click to unmute (plays a test tone)'"
            @click="toggleSound"
          >
            {{ soundEnabled ? '🔊 Sound on' : '🔇 Sound off' }}
          </button>
          <button v-if="!sessionActive" class="primary" :disabled="busy || calibratedCount < 2" @click="start">
            {{ busy ? 'Starting…' : 'Start detection' }}
          </button>
          <button v-else class="ghost" :disabled="busy" @click="stop">
            {{ busy ? 'Stopping…' : 'Stop' }}
          </button>
        </div>
      </div>
      <p v-if="calibratedCount < 2" class="status error">
        Need at least 2 calibrated cameras — {{ calibratedCount }} calibrated so far. Finish calibration first.
      </p>
      <p v-if="error" class="status error">{{ error }}</p>
    </div>

    <div
      v-if="latestHit"
      class="card hit-card"
      :class="{ accepted: latestHit.accepted, review: latestHit.review_required, 'near-wire': nearWire(latestHit) }"
    >
      <div class="hit-body">
        <DartboardFace
          v-if="geometry"
          :geometry="geometry"
          :size="360"
          :markers="latestHitMarkers"
          class="hit-board"
        />
        <div class="hit-main">
          <div class="hit-label">{{ latestHit.label }}</div>
          <div class="hit-details">
            <span>{{ latestHit.score }} pts</span>
            <span>{{ Math.round(latestHit.confidence * 100) }}% confidence</span>
            <span>cameras {{ latestHit.cameras_used.join(', ') }}</span>
            <span v-if="!latestHit.accepted">{{ latestHit.review_required ? 'needs confirmation' : 'rejected' }}</span>
          </div>
          <p v-if="nearWire(latestHit)" class="wire-warning">
            ⚠ Close to a wire ({{ latestHit.wire_distance_mm.toFixed(1) }}mm, within the
            {{ latestHit.positional_uncertainty_mm.toFixed(1) }}mm measurement uncertainty) — the neighbouring
            segment is a real possibility. Worth a manual check.
          </p>
          <p class="muted hit-reason">{{ latestHit.reason }}</p>
          <ul v-if="Object.keys(latestHit.camera_notes || {}).length" class="camera-notes">
            <li v-for="(note, cid) in latestHit.camera_notes" :key="cid">
              <strong>{{ cameraName(Number(cid)) }} not used:</strong> {{ note }}
            </li>
          </ul>
          <p v-if="latestHit.correction" class="mono correction-mark">
            You corrected this: actually {{ latestHit.correction.label }}
          </p>
          <button v-else-if="geometry" class="ghost" @click="openBoardCorrection(latestHit)">
            Correct this throw
          </button>
        </div>
      </div>
    </div>

    <p v-if="slots.length === 0" class="muted">
      No cameras assigned yet — set them up on the
      <router-link to="/setup">Setup</router-link> page.
    </p>

    <div class="camera-grid" :class="{ 'has-expanded': expandedCamera !== null }">
      <div
        v-for="s in slots"
        v-show="expandedCamera === null || expandedCamera === s.deviceId"
        :key="s.deviceId"
        class="card det-card"
        :class="{ expanded: expandedCamera === s.deviceId }"
      >
        <div class="det-head">
          <h3>{{ s.name }}</h3>
          <span
            v-if="cameraHealth[s.deviceId] && !cameraHealth[s.deviceId].delivering"
            class="status error"
            :title="cameraHealth[s.deviceId].error || 'no frames received from this camera'"
          >no signal</span>
          <span v-else class="status" :class="calibratedIds.has(s.deviceId) ? 'ok' : 'muted'">
            {{ calibratedIds.has(s.deviceId) ? 'calibrated' : 'not calibrated' }}
          </span>
        </div>
        <p v-if="cameraHealth[s.deviceId] && !cameraHealth[s.deviceId].delivering" class="status error dead-cam">
          Not sending frames — this camera is being ignored.
          {{ cameraHealth[s.deviceId].error || 'The device is retrying in the background.' }}
        </p>

        <div
          class="det-preview clickable"
          :title="expandedCamera === s.deviceId ? 'Click to shrink' : 'Click to enlarge'"
          @click="toggleExpanded(s.deviceId)"
        >
          <span class="expand-hint">{{ expandedCamera === s.deviceId ? '✕ shrink' : '⛶ enlarge' }}</span>
          <img
            :src="`/api/cameras/${s.deviceId}/stream`"
            :alt="s.name"
            @load="onImgLoad(s.deviceId, $event)"
          />
          <CalibrationGrid
            v-if="showGrid && naturalSizes[s.deviceId] && grids[s.deviceId]"
            :grid="grids[s.deviceId]"
            :natural-width="naturalSizes[s.deviceId].w"
            :natural-height="naturalSizes[s.deviceId].h"
          />
          <svg
            v-if="naturalSizes[s.deviceId] && hitPixels[s.deviceId]"
            class="det-svg"
            :viewBox="`0 0 ${naturalSizes[s.deviceId].w} ${naturalSizes[s.deviceId].h}`"
            preserveAspectRatio="xMidYMid meet"
          >
            <circle
              :cx="hitPixels[s.deviceId][0]"
              :cy="hitPixels[s.deviceId][1]"
              r="13"
              class="hit-ring"
            />
            <line
              :x1="hitPixels[s.deviceId][0] - 22" :y1="hitPixels[s.deviceId][1]"
              :x2="hitPixels[s.deviceId][0] + 22" :y2="hitPixels[s.deviceId][1]"
              class="hit-cross"
            />
            <line
              :x1="hitPixels[s.deviceId][0]" :y1="hitPixels[s.deviceId][1] - 22"
              :x2="hitPixels[s.deviceId][0]" :y2="hitPixels[s.deviceId][1] + 22"
              class="hit-cross"
            />
            <text
              :x="hitPixels[s.deviceId][0] + 26" :y="hitPixels[s.deviceId][1] - 10"
              class="hit-text"
            >{{ latestHit?.label }}</text>
          </svg>
          <svg
            v-if="naturalSizes[s.deviceId] && candidateLines[s.deviceId]"
            class="det-svg"
            :viewBox="`0 0 ${naturalSizes[s.deviceId].w} ${naturalSizes[s.deviceId].h}`"
            preserveAspectRatio="xMidYMid meet"
          >
            <line
              :x1="candidateLines[s.deviceId].line[0]"
              :y1="candidateLines[s.deviceId].line[1]"
              :x2="candidateLines[s.deviceId].line[2]"
              :y2="candidateLines[s.deviceId].line[3]"
              class="axis-line"
            />
            <text
              :x="candidateLines[s.deviceId].line[0] + 12"
              :y="candidateLines[s.deviceId].line[1]"
              class="axis-label"
            >{{ Math.round(candidateLines[s.deviceId].confidence * 100) }}%</text>
          </svg>
        </div>
      </div>
    </div>

    <!-- Takeout is the only event with no dart to show for it, so it gets its
         own panel: what fired, why, and whether the game was actually waiting. -->
    <div class="card takeout-card" :class="{ flash: takeoutFlash }">
      <div class="takeout-head">
        <h3>Darts removed (takeout) events</h3>
        <span class="takeout-count">{{ takeouts.length }} recorded</span>
      </div>
      <p class="muted takeout-intro">
        Fires when the cameras see the darts come out. While the game is waiting for a
        takeout the detector is deliberately far more sensitive — a dart can't be scored
        then, so anything moving on the board is the takeout.
      </p>
      <p v-if="!takeouts.length" class="muted">
        Nothing yet. Throw a turn, then pull the darts out and watch this panel.
      </p>
      <div v-else class="takeout-list">
        <div v-for="t in takeouts" :key="t.id ?? t.ts" class="takeout-row" :class="{ awaiting: t.awaiting }">
          <span class="takeout-time">{{ new Date(t.ts).toLocaleTimeString() }}</span>
          <div class="takeout-detail">
            <strong>{{ t.reason ?? 'takeout' }}</strong>
            <span class="muted">
              cameras {{ (t.camera_ids ?? []).join(', ') || '—' }}
              <template v-if="t.occupancy != null"> · board {{ t.occupancy }}% covered</template>
            </span>
          </div>
          <span class="takeout-tag" :class="t.awaiting ? 'expected' : 'unprompted'">
            {{ t.awaiting ? 'turn was over' : 'mid-turn' }}
          </span>
        </div>
      </div>
    </div>

    <div v-if="log.length" class="card">
      <h3>Event log</h3>
      <div class="det-log">
        <div v-for="(entry, i) in log" :key="i" class="det-log-entry" :class="entry.kind">
          <span class="det-log-time">{{ new Date(entry.ts).toLocaleTimeString() }}</span>
          <span>{{ entry.text }}</span>
        </div>
      </div>
    </div>

    <div v-if="history.length" class="card">
      <div class="history-head">
        <div>
          <h3>Throw history</h3>
          <p class="muted" style="margin-top: -0.4rem">
            Kept so a wrong call can be checked afterwards — click "Correct" on a wrong throw and mark
            exactly where it really landed, either on the board diagram or on the actual camera photo.
          </p>
        </div>
        <button class="ghost" :disabled="analyzing" @click="runAnalyze">
          {{ analyzing ? 'Analyzing…' : 'Analyze corrections' }}
        </button>
      </div>
      <div class="history-table-wrap">
        <table class="history-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Label</th>
              <th>Conf.</th>
              <th>Cameras</th>
              <th>Wire dist.</th>
              <th>Uncertainty</th>
              <th>Status</th>
              <th>Correction</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(h, i) in history" :key="i" :class="{ 'row-near-wire': nearWire(h) }">
              <td class="mono">{{ new Date(h.ts * 1000).toLocaleTimeString() }}</td>
              <td class="mono">{{ h.label }}</td>
              <td class="mono">{{ Math.round(h.confidence * 100) }}%</td>
              <td class="mono">{{ h.cameras_used.join(',') }}</td>
              <td class="mono">{{ h.wire_distance_mm.toFixed(1) }}mm</td>
              <td class="mono">{{ h.positional_uncertainty_mm.toFixed(1) }}mm</td>
              <td>
                <span v-if="nearWire(h)">near wire</span>
                <span v-else-if="!h.accepted">{{ h.review_required ? 'review' : 'rejected' }}</span>
                <span v-else>accepted</span>
              </td>
              <td>
                <span v-if="h.correction" class="mono correction-mark">
                  actually {{ h.correction.label }}
                </span>
                <div v-else class="correction-actions">
                  <button v-if="geometry" class="ghost small" @click="openBoardCorrection(h)">Correct</button>
                  <button
                    v-if="(h.evidence_cameras?.length ?? 0) > 0"
                    class="ghost small"
                    @click="openCorrection(h)"
                  >
                    from photo
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-if="analyzeError" class="status error" style="margin-top: 0.6rem">{{ analyzeError }}</p>
      <div v-if="analysis" class="analysis-panel">
        <p v-if="analysis.count === 0" class="muted">No corrections recorded yet — correct a throw first.</p>
        <template v-else>
          <div class="analysis-stats">
            <span><strong>{{ analysis.count }}</strong> corrected</span>
            <span><strong>{{ analysis.segment_mismatches }}</strong> wrong segment</span>
            <span>
              <strong>{{ analysis.mismatches_flagged_uncertain }}</strong> of those were flagged "near a wire"
            </span>
            <span class="warn">
              <strong>{{ analysis.mismatches_not_flagged }}</strong> were wrong AND confidently accepted
            </span>
            <span v-if="analysis.mean_error_mm !== null">
              mean position error <strong>{{ analysis.mean_error_mm.toFixed(1) }}mm</strong>
            </span>
            <span v-if="analysis.max_error_mm !== null">
              max <strong>{{ analysis.max_error_mm.toFixed(1) }}mm</strong>
            </span>
          </div>
        </template>
      </div>
    </div>

    <DartCorrection
      v-if="correctingEvent"
      :event-id="correctingEvent.eventId"
      :detected-label="correctingEvent.detectedLabel"
      :cameras="correctingEvent.cameras"
      @saved="applyCorrectedEntry"
      @cancel="correctingEvent = null"
    />

    <DartboardCorrection
      v-if="boardCorrecting && geometry"
      :event-id="boardCorrecting.eventId"
      :detected-label="boardCorrecting.detectedLabel"
      :detected="boardCorrecting.detected"
      :geometry="geometry"
      @saved="applyCorrectedEntry"
      @cancel="boardCorrecting = null"
    />
  </div>
</template>

<style scoped>
.session-card {
  margin-bottom: 1rem;
}

.session-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.session-message {
  margin: 0.3rem 0 0;
}

.hit-card {
  margin-bottom: 1rem;
  border-left: 4px solid var(--border);
}

.hit-card.accepted {
  border-left-color: var(--accent);
}

.hit-card.review {
  border-left-color: #e8a23d;
}

.hit-card.near-wire {
  border-left-color: #e8a23d;
}

.hit-body {
  display: flex;
  gap: 1.2rem;
  align-items: flex-start;
  flex-wrap: wrap;
}

.hit-board {
  flex-shrink: 0;
}

.hit-main {
  flex: 1 1 260px;
  min-width: 0;
}

.wire-warning {
  margin: 0.5rem 0 0;
  padding: 0.5rem 0.7rem;
  border-radius: 6px;
  background: rgba(232, 162, 61, 0.12);
  color: #e8a23d;
  font-size: 0.85rem;
}

.hit-label {
  font-family: ui-monospace, monospace;
  font-size: 2rem;
  font-weight: 700;
}

.hit-details {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 0.9rem;
  margin-top: 0.3rem;
}

.hit-reason {
  margin: 0.5rem 0 0;
  font-size: 0.85rem;
}

.dead-cam {
  margin: 0;
  font-size: 0.8rem;
}

.camera-notes {
  margin: 0.4rem 0 0;
  padding-left: 1.1rem;
  font-size: 0.8rem;
  color: var(--muted);
}

.det-card {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.det-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}

.det-head h3 {
  margin: 0;
}

/* One expanded camera takes the full row so the axis overlay is actually
   readable; the others are hidden rather than reflowed around it. */
.camera-grid.has-expanded {
  display: block;
}

.det-card.expanded .det-preview {
  max-height: 78vh;
}

.det-card.expanded .det-preview img {
  max-height: 78vh;
  object-fit: contain;
  margin: 0 auto;
}

.det-preview {
  position: relative;
  width: 100%;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
}

.det-preview.clickable {
  cursor: zoom-in;
}

.det-card.expanded .det-preview.clickable {
  cursor: zoom-out;
}

.expand-hint {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  z-index: 5;
  padding: 0.15rem 0.5rem;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 0.75rem;
  font-family: ui-monospace, monospace;
  opacity: 0;
  transition: opacity 0.15s;
  pointer-events: none;
}

.det-preview.clickable:hover .expand-hint {
  opacity: 1;
}

.det-preview img {
  width: 100%;
  height: auto;
  display: block;
}

.det-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.axis-line {
  stroke: #ff3ec8;
  stroke-width: 4;
  stroke-linecap: round;
}

/* Where the fused hit landed, drawn in each camera's own view so the call
   can be checked against the real wires. Yellow to stay distinct from the
   cyan rings and magenta wedge lines of the calibration grid. */
.hit-ring {
  fill: none;
  stroke: #ffd400;
  stroke-width: 3;
}

.hit-cross {
  stroke: #ffd400;
  stroke-width: 2;
}

.hit-text {
  fill: #ffd400;
  stroke: #000;
  stroke-width: 4;
  paint-order: stroke;
  font-size: 30px;
  font-weight: 700;
  font-family: ui-monospace, monospace;
}

.axis-label {
  fill: #ff3ec8;
  stroke: #000;
  stroke-width: 3;
  paint-order: stroke;
  font-size: 22px;
  font-family: ui-monospace, monospace;
}

/* ---- takeout diagnostics ---- */
.awaiting-chip,
.clearing-chip {
  display: inline-block;
  margin-left: 0.5rem;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.68rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  vertical-align: middle;
}

.awaiting-chip {
  border: 1px solid #ffbf4d;
  background: rgba(255, 191, 77, 0.14);
  color: #ffbf4d;
}

.clearing-chip {
  border: 1px solid #38d9f1;
  background: rgba(56, 217, 241, 0.14);
  color: #38d9f1;
}

.session-card.takeout-flash {
  border-color: #ff5f69;
  box-shadow: 0 0 26px rgba(255, 95, 105, 0.4);
}

.takeout-card {
  margin-top: 1rem;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.takeout-card.flash {
  border-color: #ff5f69;
  box-shadow: 0 0 30px rgba(255, 95, 105, 0.45);
}

.takeout-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.6rem;
}

.takeout-head h3 {
  margin: 0;
}

.takeout-count {
  color: var(--muted);
  font-size: 0.78rem;
}

.takeout-intro {
  margin: 0.4rem 0 0.7rem;
  font-size: 0.8rem;
  line-height: 1.5;
}

.takeout-list {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  max-height: 320px;
  overflow-y: auto;
}

.takeout-row {
  display: grid;
  grid-template-columns: 84px 1fr auto;
  gap: 0.6rem;
  align-items: center;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--border);
  border-left: 3px solid #ff5f69;
  border-radius: 8px;
  background: var(--panel-2);
}

.takeout-row.awaiting {
  border-left-color: var(--accent);
}

.takeout-time {
  color: var(--muted);
  font-family: ui-monospace, monospace;
  font-size: 0.74rem;
}

.takeout-detail {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.takeout-detail strong {
  font-size: 0.85rem;
}

.takeout-detail span {
  font-size: 0.72rem;
}

.takeout-tag {
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.66rem;
  font-weight: 800;
  letter-spacing: 0.06em;
  white-space: nowrap;
}

.takeout-tag.expected {
  border: 1px solid var(--accent);
  color: var(--accent);
}

.takeout-tag.unprompted {
  border: 1px solid #ffbf4d;
  color: #ffbf4d;
}

.det-log {
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.det-log-entry {
  display: flex;
  gap: 0.6rem;
  font-size: 0.85rem;
  color: var(--text);
}

.det-log-entry.review {
  color: #e8a23d;
}

.det-log-entry.takeout {
  color: var(--muted);
  font-style: italic;
}

.det-log-entry.near-wire {
  color: #e8a23d;
}

.det-log-time {
  font-family: ui-monospace, monospace;
  color: var(--muted);
  flex-shrink: 0;
}

.history-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.history-head h3 {
  margin: 0 0 0.2rem;
}

.history-table-wrap {
  overflow-x: auto;
}

.history-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.history-table th {
  text-align: left;
  color: var(--muted);
  font-weight: 500;
  padding: 0.3rem 0.6rem;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.history-table td {
  padding: 0.3rem 0.6rem;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.history-table td.mono {
  font-family: ui-monospace, monospace;
}

.history-table tr.row-near-wire td {
  color: #e8a23d;
}

.history-table button.small {
  padding: 0.15rem 0.55rem;
  font-size: 0.8rem;
}

.correction-actions {
  display: flex;
  gap: 0.4rem;
}

.correction-mark {
  color: var(--accent);
}

.analysis-panel {
  margin-top: 0.8rem;
  padding-top: 0.7rem;
  border-top: 1px solid var(--border);
}

.analysis-stats {
  display: flex;
  gap: 1.2rem;
  flex-wrap: wrap;
  font-size: 0.9rem;
}

.analysis-stats .warn {
  color: #e8a23d;
}
</style>
