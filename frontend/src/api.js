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
  getSettings: () => request('/api/settings'),
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

  // Detection is Autodarts now: this is its health, consumed by the play
  // screen's status chip. All the old camera-CV detection/calibration/correction
  // endpoints are gone - Autodarts owns detection and its own calibration UI.
  getAutodartsStatus: () => request('/api/detection/autodarts'),
  resetBoard: () => request('/api/detection/autodarts/reset', { method: 'POST' }),

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

  // Static board geometry for drawing the live board diagram (DartboardFace).
  getBoardGeometry: () => request('/api/detection/board-geometry'),
}
