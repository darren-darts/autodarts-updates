// Synthesised UI sounds via Web Audio - no audio files, so nothing to load,
// nothing to cache, and it works with no network at all (the app is meant to
// run on a Pi by the board).
//
// The point of having distinct sounds: you're at the oche, 2.4m from the
// screen, and can't read it. A bright rising pair means "scored it, carry
// on"; a duller flat tone means "look at the screen before you throw again";
// a descending three-note run means "darts out, you're done, next player".
//
// They are separated by *contour*, not just pitch - rising, flat, falling -
// because that survives a noisy pub far better than a difference in tone.

let ctx = null

function context() {
  if (ctx) return ctx
  const Ctor = window.AudioContext || window.webkitAudioContext
  if (!Ctor) return null // very old browser - silently no sound rather than an error
  ctx = new Ctor()
  return ctx
}

/** Browsers refuse to start audio until the page has had a real user
 *  gesture. Call this from a click handler so later event-driven sounds
 *  (which have no gesture of their own) are allowed to play. */
export function unlockAudio() {
  const audio = context()
  if (audio && audio.state === 'suspended') audio.resume().catch(() => {})
}

function tone(audio, { freq, start, duration, peak = 0.18, type = 'triangle' }) {
  const osc = audio.createOscillator()
  const gain = audio.createGain()
  osc.type = type
  osc.frequency.setValueAtTime(freq, start)

  // Ramped envelope, never an abrupt gain change - switching gain instantly
  // produces an audible click. exponentialRamp can't reach exactly 0, hence
  // the small floor.
  gain.gain.setValueAtTime(0.0001, start)
  gain.gain.exponentialRampToValueAtTime(peak, start + 0.012)
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration)

  osc.connect(gain).connect(audio.destination)
  osc.start(start)
  osc.stop(start + duration + 0.02)
}

/** Clean, confidently-scored dart: a quick rising two-note blip. */
export function playHitSound() {
  const audio = context()
  if (!audio) return
  if (audio.state === 'suspended') audio.resume().catch(() => {})
  const t = audio.currentTime
  tone(audio, { freq: 880, start: t, duration: 0.09 })          // A5
  tone(audio, { freq: 1318.5, start: t + 0.075, duration: 0.16 }) // E6
}

/** Detected but not trusted (rejected, or sitting on a wire): one flat,
 *  lower note - deliberately unsatisfying, so it's obvious without looking. */
export function playReviewSound() {
  const audio = context()
  if (!audio) return
  if (audio.state === 'suspended') audio.resume().catch(() => {})
  const t = audio.currentTime
  tone(audio, { freq: 320, start: t, duration: 0.26, peak: 0.14, type: 'sine' })
}

/** Darts pulled out of the board: a descending three-note run, the clear
 *  opposite of the rising "scored" blip. Softer and slightly longer, because
 *  it marks the end of a turn rather than demanding attention. */
export function playTakeoutSound() {
  const audio = context()
  if (!audio) return
  if (audio.state === 'suspended') audio.resume().catch(() => {})
  const t = audio.currentTime
  tone(audio, { freq: 784, start: t, duration: 0.11, peak: 0.15 })            // G5
  tone(audio, { freq: 587.3, start: t + 0.085, duration: 0.12, peak: 0.15 })  // D5
  tone(audio, { freq: 392, start: t + 0.175, duration: 0.26, peak: 0.16 })    // G4
}
