// FastAPI reports its own errors two different ways: a plain string for
// HTTPException(detail="..."), but an ARRAY of {loc, msg, type} objects for
// request-validation (422) failures. Passing the array straight to
// new Error() is what renders as the useless "[object Object]".
function describeError(detail, fallback) {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item
        // Drop the "body"/"path" wrapper so the field name reads naturally.
        const field = Array.isArray(item?.loc)
          ? item.loc.filter((p) => p !== 'body' && p !== 'path' && p !== 'query').join('.')
          : ''
        const message = item?.msg || JSON.stringify(item)
        return field ? `${field}: ${message}` : message
      })
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  if (detail && typeof detail === 'object') return detail.msg || JSON.stringify(detail)
  return fallback
}

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = describeError((await res.json())?.detail, res.statusText)
    } catch { /* not json */ }
    throw new Error(detail || `Request failed (${res.status})`)
  }
  return res.json()
}

export const api = {
  getCameras: () => request('/api/cameras'),
  getSettings: () => request('/api/settings'),
  saveCameraSlots: (slots) =>
    request('/api/settings/cameras', {
      method: 'PUT',
      body: JSON.stringify({ slots }),
    }),
  getLedStatus: () => request('/api/leds/status'),
  getLedPorts: () => request('/api/leds/ports'),
  getLedEffects: () => request('/api/leds/effects'),
  getLedCues: () => request('/api/leds/cues'),
  sendLedState: (state) =>
    request('/api/leds/state', { method: 'POST', body: JSON.stringify(state) }),
  fireLedCue: (name) =>
    request(`/api/leds/cue/${encodeURIComponent(name)}`, { method: 'POST' }),
  saveLedSettings: (led) =>
    request('/api/settings/leds', { method: 'PUT', body: JSON.stringify(led) }),
  getNetworkInfo: () => request('/api/network/info'),

  getPlayers: () => request('/api/players'),
  addPlayer: () => request('/api/players', { method: 'POST' }),
  removePlayer: (id) => request(`/api/players/${id}`, { method: 'DELETE' }),
  updatePlayer: (id, body) =>
    request(`/api/players/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  clearSelfie: (id) => request(`/api/players/${id}/selfie`, { method: 'DELETE' }),
  async uploadSelfie(id, blob) {
    // Bypasses request(): a multipart body needs the browser to set its own
    // Content-Type with boundary, which request()'s JSON header would break.
    const form = new FormData()
    form.append('file', blob, 'selfie.jpg')
    const res = await fetch(`/api/players/${id}/selfie`, { method: 'POST', body: form })
    if (!res.ok) {
      let detail = res.statusText
      try {
        detail = (await res.json()).detail ?? detail
      } catch {
        /* not json */
      }
      throw new Error(detail)
    }
    return res.json()
  },

  getAllCalibrations: () => request('/api/calibration'),
  getCalibration: (id) => request(`/api/calibration/${id}`),
  autoCalibrate: (id) => request(`/api/calibration/${id}/auto`, { method: 'POST' }),
  fineTuneCalibration: (id) => request(`/api/calibration/${id}/fine-tune`, { method: 'POST' }),
  saveCalibrationPoints: (id, points) =>
    request(`/api/calibration/${id}/points`, { method: 'PUT', body: JSON.stringify({ points }) }),
  clearCalibration: (id) => request(`/api/calibration/${id}`, { method: 'DELETE' }),

  startDetection: () => request('/api/detection/start', { method: 'POST' }),
  stopDetection: () => request('/api/detection/stop', { method: 'POST' }),
  getDetectionStatus: () => request('/api/detection/status'),
  correctDetection: (eventId, cameraId, xPx, yPx) =>
    request(`/api/detection/history/${eventId}/correct`, {
      method: 'POST',
      body: JSON.stringify({ camera_id: cameraId, x_px: xPx, y_px: yPx }),
    }),
  correctDetectionOnBoard: (eventId, xMm, yMm) =>
    request(`/api/detection/history/${eventId}/correct`, {
      method: 'POST',
      body: JSON.stringify({ x_mm: xMm, y_mm: yMm }),
    }),
  analyzeDetection: () => request('/api/detection/analyze'),
  getGameCatalogue: () => request('/api/games/catalogue'),
  getGameState: () => request('/api/games/state'),
  startGame: (slug, difficulty, playerIds, options = null) =>
    request('/api/games/start', {
      method: 'POST',
      body: JSON.stringify({ slug, difficulty, player_ids: playerIds, options }),
    }),
  getDisplay: () => request('/api/display'),
  setPresentation: (enabled) =>
    request('/api/display/presentation', { method: 'POST', body: JSON.stringify({ enabled }) }),
  stopGame: () => request('/api/games/stop', { method: 'POST' }),
  nextTurn: () => request('/api/games/next-turn', { method: 'POST' }),
  confirmTakeout: () => request('/api/games/confirm-takeout', { method: 'POST' }),
  previousTurn: () => request('/api/games/previous-turn', { method: 'POST' }),
  undoDart: () => request('/api/games/undo', { method: 'POST' }),
  sendManualDart: (segment, multiplier) =>
    request('/api/games/dart', {
      method: 'POST',
      body: JSON.stringify({ segment, multiplier }),
    }),

  getUpdateStatus: () => request('/api/update/status'),
  checkForUpdate: () => request('/api/update/check', { method: 'POST' }),
  downloadUpdate: () => request('/api/update/download', { method: 'POST' }),
  cancelUpdate: () => request('/api/update/cancel', { method: 'POST' }),
  discardUpdate: () => request('/api/update/discard', { method: 'POST' }),
  restartForUpdate: () => request('/api/update/restart', { method: 'POST' }),
  saveUpdateSettings: (settings) =>
    request('/api/update/settings', { method: 'POST', body: JSON.stringify(settings) }),

  getBoardGeometry: () => request('/api/detection/board-geometry'),
  projectBoardPoint: (xMm, yMm) =>
    request('/api/detection/project', { method: 'POST', body: JSON.stringify({ x_mm: xMm, y_mm: yMm }) }),
}
