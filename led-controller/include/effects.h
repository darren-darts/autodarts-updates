#pragma once
#include <FastLED.h>

enum EffectMode {
  FX_SOLID = 0,
  FX_RAINBOW,
  FX_RAINBOW_CYCLE,
  FX_THEATER_CHASE,
  FX_THEATER_CHASE_RAINBOW,
  FX_COLOR_WIPE,
  FX_COLOR_WIPE_RANDOM,
  FX_SCAN,
  FX_DUAL_SCAN,
  FX_TWINKLE,
  FX_SPARKLE,
  FX_BREATHE,
  FX_STROBE,
  FX_FIRE,
  FX_COMET,
  FX_METEOR,
  FX_RUNNING_LIGHTS,
  FX_CONFETTI,
  FX_JUGGLE,
  FX_BPM,
  FX_FADE,
  FX_CHASE_RAINBOW,
  // --- solid colour presets (ignore state.color, always this colour) ------
  FX_RED,
  FX_GREEN,
  FX_BLUE,
  FX_WHITE,
  FX_YELLOW,
  FX_ORANGE,
  FX_PURPLE,
  FX_CYAN,
  FX_PINK,
  // --- game feedback effects ----------------------------------------------
  FX_FLASH_3,           // 3 quick flashes of color, then hold solid (dart registered)
  FX_PULSE,             // soft breathe that never goes fully dark (waiting for throw)
  FX_HEARTBEAT,         // double-thump pulse (tension / last leg)
  FX_COUNTDOWN,         // lit ring drains pixel by pixel like a timer
  FX_SPINNER,           // rotating quarter arc (processing / thinking)
  FX_CHECKER,           // alternating color/color2 pixels, swapping (attention)
  FX_POLICE,            // rotating red/blue halves (bust / foul)
  FX_BULLSEYE,          // alternating red/green pixels, swapping (bullseye hit)
  FX_CELEBRATION,       // gold + white sparkle burst (180 / winner)
  FX_WAVE,              // smooth wave blending color -> color2 around the ring
  FX_COUNT              // keep last - total number of effects
};

// Human-readable names, indexed by EffectMode. Defined in effects.cpp.
extern const char* const effectNames[FX_COUNT];

struct LedState {
  bool     on         = true;
  uint8_t  brightness = 80;          // 0-255
  uint8_t  effect     = FX_SOLID;
  uint16_t speed      = 20;          // ms between frames, lower = faster
  CRGB     color      = CRGB::White; // primary color
  CRGB     color2     = CRGB::Black; // secondary color (used by some effects)
};

// Call once from setup(), after FastLED.addLeds(...).
void effects_init(CRGB *ledArray, uint16_t totalLeds, uint16_t skip, uint16_t ringLen);

// Change the active segment at runtime (values are clamped to totalLeds).
// Clears the whole strip so no pixels from the old segment stay lit.
void effects_setSegment(uint16_t skip, uint16_t ringLen);
uint16_t effects_skip();
uint16_t effects_ringLen();

// Call every loop() iteration. Internally time-gated by state.speed,
// so it's safe (and required) to call this as often as possible.
void effects_run(LedState &state);
