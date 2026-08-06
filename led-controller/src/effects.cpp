#include "effects.h"

const char* const effectNames[FX_COUNT] = {
  "Solid",
  "Rainbow",
  "Rainbow Cycle",
  "Theater Chase",
  "Theater Chase Rainbow",
  "Color Wipe",
  "Color Wipe Random",
  "Scan",
  "Dual Scan",
  "Twinkle",
  "Sparkle",
  "Breathe",
  "Strobe",
  "Fire",
  "Comet",
  "Meteor",
  "Running Lights",
  "Confetti",
  "Juggle",
  "BPM",
  "Fade",
  "Chase Rainbow",
  "Red",
  "Green",
  "Blue",
  "White",
  "Yellow",
  "Orange",
  "Purple",
  "Cyan",
  "Pink",
  "Flash 3x",
  "Pulse",
  "Heartbeat",
  "Countdown",
  "Spinner",
  "Checker",
  "Police",
  "Bullseye",
  "Celebration",
  "Wave"
};

static CRGB   *_leds  = nullptr;
static uint16_t _total = 0;
static uint16_t _skip = 0;
static uint16_t _len  = 0;
static bool     _restartFx = false; // true for exactly one frame after the effect changes

static inline CRGB& px(uint16_t i) { return _leds[_skip + i]; }
static inline void clearSegment()  { for (uint16_t i = 0; i < _len; i++) px(i) = CRGB::Black; }

void effects_init(CRGB *ledArray, uint16_t totalLeds, uint16_t skip, uint16_t ringLen) {
  _leds  = ledArray;
  _total = totalLeds;
  effects_setSegment(skip, ringLen);
}

void effects_setSegment(uint16_t skip, uint16_t ringLen) {
  if (skip >= _total) skip = _total - 1;
  if (ringLen < 1) ringLen = 1;
  if (skip + ringLen > _total) ringLen = _total - skip;
  _skip = skip;
  _len  = ringLen;
  fill_solid(_leds, _total, CRGB::Black); // wipe pixels left over from the old segment
}

uint16_t effects_skip()    { return _skip; }
uint16_t effects_ringLen() { return _len; }

// ---------------------------------------------------------------------------
// Individual effects. Each owns its own static "animation position" state
// and is called once per frame (frame pacing is handled in effects_run).
// ---------------------------------------------------------------------------

static void fxSolid(LedState &s) {
  for (uint16_t i = 0; i < _len; i++) px(i) = s.color;
}

static void fxRainbow(LedState &s) {
  for (uint16_t i = 0; i < _len; i++) {
    uint8_t hue = (uint16_t)(i * 256) / _len;
    px(i) = CHSV(hue, 255, 255);
  }
}

static void fxRainbowCycle(LedState &s) {
  static uint8_t startHue = 0;
  for (uint16_t i = 0; i < _len; i++) {
    uint8_t hue = startHue + (uint16_t)(i * 256) / _len;
    px(i) = CHSV(hue, 255, 255);
  }
  startHue++;
}

static void fxTheaterChase(LedState &s) {
  static uint8_t offset = 0;
  clearSegment();
  for (uint16_t i = offset; i < _len; i += 3) px(i) = s.color;
  offset = (offset + 1) % 3;
}

static void fxTheaterChaseRainbow(LedState &s) {
  static uint8_t offset = 0;
  static uint8_t hue = 0;
  clearSegment();
  for (uint16_t i = offset; i < _len; i += 3) px(i) = CHSV(hue, 255, 255);
  offset = (offset + 1) % 3;
  hue += 4;
}

static void fxColorWipe(LedState &s) {
  static uint16_t pos = 0;
  static bool wipingOn = true;
  px(pos) = wipingOn ? s.color : CRGB::Black;
  pos++;
  if (pos >= _len) { pos = 0; wipingOn = !wipingOn; }
}

static void fxColorWipeRandom(LedState &s) {
  static uint16_t pos = 0;
  static CRGB currentColor = CRGB::Red;
  if (pos == 0) currentColor = CHSV(random8(), 255, 255);
  px(pos) = currentColor;
  pos = (pos + 1) % _len;
}

static void fxScan(LedState &s) {
  static int16_t pos = 0;
  static int8_t dir = 1;
  clearSegment();
  px(pos) = s.color;
  pos += dir;
  if (pos >= (int16_t)_len - 1 || pos <= 0) dir = -dir;
}

static void fxDualScan(LedState &s) {
  static int16_t pos = 0;
  static int8_t dir = 1;
  clearSegment();
  px(pos) = s.color;
  px(_len - 1 - pos) = s.color2 == CRGB::Black ? s.color : s.color2;
  pos += dir;
  if (pos >= (int16_t)_len - 1 || pos <= 0) dir = -dir;
}

static void fxTwinkle(LedState &s) {
  fadeToBlackBy(&px(0), _len, 20);
  if (random8() < 60) px(random16(_len)) = s.color;
}

static void fxSparkle(LedState &s) {
  clearSegment();
  for (uint16_t i = 0; i < _len; i++) px(i) = s.color;
  px(random16(_len)) = CRGB::White;
}

static void fxBreathe(LedState &s) {
  static uint8_t bpmPhase = 0;
  uint8_t b = sin8(bpmPhase);          // smooth 0-255-0 wave
  for (uint16_t i = 0; i < _len; i++) {
    px(i) = s.color;
    px(i).nscale8_video(b);
  }
  bpmPhase += 3;
}

static void fxStrobe(LedState &s) {
  static bool flashOn = false;
  for (uint16_t i = 0; i < _len; i++) px(i) = flashOn ? s.color : CRGB::Black;
  flashOn = !flashOn;
}

static void fxFire(LedState &s) {
  // classic Fire2012, cooling/sparking constants tuned for a short strip
  static byte heat[512];
  uint16_t len = _len > 512 ? 512 : _len;
  for (uint16_t i = 0; i < len; i++) {
    heat[i] = qsub8(heat[i], random8(0, ((55 * 10) / len) + 2));
  }
  for (uint16_t k = len - 1; k >= 2; k--) {
    heat[k] = (heat[k - 1] + heat[k - 2] + heat[k - 2]) / 3;
  }
  if (random8() < 120) {
    int y = random8(7);
    heat[y] = qadd8(heat[y], random8(160, 255));
  }
  for (uint16_t j = 0; j < len; j++) {
    px(j) = HeatColor(heat[j]);
  }
}

static void fxComet(LedState &s) {
  static int16_t pos = 0;
  fadeToBlackBy(&px(0), _len, 60);
  px(pos) = s.color;
  pos = (pos + 1) % _len;
}

static void fxMeteor(LedState &s) {
  static uint16_t pos = 0;
  fadeToBlackBy(&px(0), _len, 64);
  for (int8_t i = 0; i < 4; i++) {
    int16_t idx = pos - i;
    if (idx >= 0 && idx < (int16_t)_len) px(idx) = s.color;
  }
  pos = (pos + 1) % (_len + 8);
}

static void fxRunningLights(LedState &s) {
  static uint16_t phase = 0;
  for (uint16_t i = 0; i < _len; i++) {
    uint8_t b = sin8((i * 20) + phase);
    CRGB c = s.color;
    c.nscale8_video(b);
    px(i) = c;
  }
  phase += 6;
}

static void fxConfetti(LedState &s) {
  fadeToBlackBy(&px(0), _len, 10);
  px(random16(_len)) = CHSV(random8(), 200, 255);
}

static void fxJuggle(LedState &s) {
  fadeToBlackBy(&px(0), _len, 20);
  uint8_t dothue = 0;
  for (uint8_t i = 0; i < 8; i++) {
    px(beatsin16(i + 7, 0, _len - 1)) |= CHSV(dothue, 200, 255);
    dothue += 32;
  }
}

static void fxBpm(LedState &s) {
  uint8_t bpm = 30;
  CRGBPalette16 palette = PartyColors_p;
  uint8_t beat = beatsin8(bpm, 64, 255);
  for (uint16_t i = 0; i < _len; i++) {
    px(i) = ColorFromPalette(palette, i * 2, beat - (i * 2));
  }
}

static void fxFade(LedState &s) {
  static uint8_t b = 0;
  static int8_t dir = 5;
  for (uint16_t i = 0; i < _len; i++) {
    CRGB c = s.color;
    c.nscale8_video(b);
    px(i) = c;
  }
  b += dir;
  if (b >= 250 || b <= 5) dir = -dir;
}

static void fxChaseRainbow(LedState &s) {
  static uint16_t pos = 0;
  static uint8_t hue = 0;
  fadeToBlackBy(&px(0), _len, 40);
  px(pos) = CHSV(hue, 255, 255);
  pos = (pos + 1) % _len;
  hue += 8;
}

// --- solid colour presets ---------------------------------------------------

static void fxSolidColor(CRGB c) {
  for (uint16_t i = 0; i < _len; i++) px(i) = c;
}

// --- game feedback effects --------------------------------------------------

// 3 quick on/off flashes of state.color, then hold solid. Re-triggered by
// switching to another effect and back. state.speed sets the flash rate
// (sx ~100 gives 3 flashes in ~0.6s).
static void fxFlash3(LedState &s) {
  static uint8_t step = 0;
  if (_restartFx) step = 0;
  bool on = (step >= 6) || (step % 2 == 0);
  for (uint16_t i = 0; i < _len; i++) px(i) = on ? s.color : CRGB::Black;
  if (step < 6) step++;
}

// Like Breathe but never fully dark - reads as "idle / waiting" rather than off.
static void fxPulse(LedState &s) {
  static uint8_t phase = 0;
  uint8_t b = scale8(sin8(phase), 195) + 60;   // 60-255
  for (uint16_t i = 0; i < _len; i++) {
    px(i) = s.color;
    px(i).nscale8_video(b);
  }
  phase += 3;
}

// Double-thump like a heartbeat: bright-bright-rest.
static void fxHeartbeat(LedState &s) {
  static uint8_t t = 0;
  uint8_t b = (t < 4 || (t >= 7 && t < 11)) ? 255 : 30;
  for (uint16_t i = 0; i < _len; i++) {
    px(i) = s.color;
    px(i).nscale8_video(b);
  }
  t = (t + 1) % 40;
}

// Fully lit ring drains one pixel per frame, then refills and repeats.
// state.speed sets the pace: sx = totalMs / ringLen for a one-shot timer feel.
static void fxCountdown(LedState &s) {
  static int16_t remaining = -1;
  if (_restartFx || remaining < 0) remaining = _len;
  for (uint16_t i = 0; i < _len; i++) px(i) = (int16_t)i < remaining ? s.color : CRGB::Black;
  remaining--;
}

// Rotating quarter arc - a "processing" indicator.
static void fxSpinner(LedState &s) {
  static uint16_t pos = 0;
  uint16_t arc = _len / 4 > 0 ? _len / 4 : 1;
  clearSegment();
  for (uint16_t k = 0; k < arc; k++) px((pos + k) % _len) = s.color;
  pos = (pos + 1) % _len;
}

// Alternating pixels of two colours, swapping every frame.
static void fxTwoColorAlternate(CRGB a, CRGB b) {
  static uint8_t offset = 0;
  for (uint16_t i = 0; i < _len; i++) px(i) = ((i + offset) & 1) ? b : a;
  offset++;
}

static void fxChecker(LedState &s) {
  // color2 defaults to black, giving alternating dots; set col2 for two colours
  fxTwoColorAlternate(s.color, s.color2);
}

// Rotating red/blue halves.
static void fxPolice(LedState &s) {
  static uint16_t offset = 0;
  for (uint16_t i = 0; i < _len; i++) {
    px(i) = ((i + offset) % _len) < _len / 2 ? CRGB::Red : CRGB::Blue;
  }
  offset = (offset + 1) % _len;
}

static void fxBullseyeFx(LedState &s) {
  fxTwoColorAlternate(CRGB::Red, CRGB::Green);
}

// Gold + white sparkle burst on a fading background.
static void fxCelebration(LedState &s) {
  fadeToBlackBy(&px(0), _len, 40);
  px(random16(_len)) = CRGB::Gold;
  if (random8() < 120) px(random16(_len)) = CRGB::White;
  if (random8() < 60)  px(random16(_len)) = CRGB::Orange;
}

// Smooth travelling wave blending color -> color2 around the ring.
static void fxWave(LedState &s) {
  static uint8_t phase = 0;
  uint8_t scale = 512 / (_len > 0 ? _len : 1); // two full waves around the ring
  for (uint16_t i = 0; i < _len; i++) {
    uint8_t w = sin8(i * scale + phase);
    px(i) = blend(s.color2, s.color, w);
  }
  phase += 4;
}

// ---------------------------------------------------------------------------
// Dispatcher
// ---------------------------------------------------------------------------
void effects_run(LedState &state) {
  static uint32_t lastFrame = 0;
  static uint8_t  lastEffect = 255; // force clear on first run / effect change

  if (!state.on) {
    clearSegment();
    FastLED.setBrightness(0);
    FastLED.show();
    return;
  }

  uint32_t now = millis();
  uint16_t interval = state.speed == 0 ? 1 : state.speed;
  if (now - lastFrame < interval) return;
  lastFrame = now;

  _restartFx = (state.effect != lastEffect);
  if (_restartFx) {
    clearSegment();
    lastEffect = state.effect;
  }

  FastLED.setBrightness(state.brightness);

  switch (state.effect) {
    case FX_SOLID:                  fxSolid(state); break;
    case FX_RAINBOW:                fxRainbow(state); break;
    case FX_RAINBOW_CYCLE:          fxRainbowCycle(state); break;
    case FX_THEATER_CHASE:          fxTheaterChase(state); break;
    case FX_THEATER_CHASE_RAINBOW:  fxTheaterChaseRainbow(state); break;
    case FX_COLOR_WIPE:             fxColorWipe(state); break;
    case FX_COLOR_WIPE_RANDOM:      fxColorWipeRandom(state); break;
    case FX_SCAN:                   fxScan(state); break;
    case FX_DUAL_SCAN:              fxDualScan(state); break;
    case FX_TWINKLE:                fxTwinkle(state); break;
    case FX_SPARKLE:                fxSparkle(state); break;
    case FX_BREATHE:                fxBreathe(state); break;
    case FX_STROBE:                 fxStrobe(state); break;
    case FX_FIRE:                   fxFire(state); break;
    case FX_COMET:                  fxComet(state); break;
    case FX_METEOR:                 fxMeteor(state); break;
    case FX_RUNNING_LIGHTS:         fxRunningLights(state); break;
    case FX_CONFETTI:               fxConfetti(state); break;
    case FX_JUGGLE:                 fxJuggle(state); break;
    case FX_BPM:                    fxBpm(state); break;
    case FX_FADE:                   fxFade(state); break;
    case FX_CHASE_RAINBOW:          fxChaseRainbow(state); break;
    case FX_RED:                    fxSolidColor(CRGB::Red); break;
    case FX_GREEN:                  fxSolidColor(CRGB::Green); break;
    case FX_BLUE:                   fxSolidColor(CRGB::Blue); break;
    case FX_WHITE:                  fxSolidColor(CRGB::White); break;
    case FX_YELLOW:                 fxSolidColor(CRGB::Yellow); break;
    case FX_ORANGE:                 fxSolidColor(CRGB(255, 100, 0)); break;
    case FX_PURPLE:                 fxSolidColor(CRGB(160, 0, 255)); break;
    case FX_CYAN:                   fxSolidColor(CRGB::Cyan); break;
    case FX_PINK:                   fxSolidColor(CRGB(255, 20, 100)); break;
    case FX_FLASH_3:                fxFlash3(state); break;
    case FX_PULSE:                  fxPulse(state); break;
    case FX_HEARTBEAT:              fxHeartbeat(state); break;
    case FX_COUNTDOWN:              fxCountdown(state); break;
    case FX_SPINNER:                fxSpinner(state); break;
    case FX_CHECKER:                fxChecker(state); break;
    case FX_POLICE:                 fxPolice(state); break;
    case FX_BULLSEYE:               fxBullseyeFx(state); break;
    case FX_CELEBRATION:            fxCelebration(state); break;
    case FX_WAVE:                   fxWave(state); break;
    default:                        fxSolid(state); break;
  }

  FastLED.show();
}
