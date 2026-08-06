#pragma once
#include <FastLED.h>

// ---------------------------------------------------------------------------
// Hardware configuration
// ---------------------------------------------------------------------------
#define DATA_PIN      14        // GPIO for LED data line.
                                // Avoid strapping pins (0, 2, 12, 15) and
                                // input-only pins (34-39) on classic ESP32.
#define NUM_LEDS      70       // total physical LEDs on the strip
#define LED_TYPE      WS2812B
#define COLOR_ORDER   GRB

// ---------------------------------------------------------------------------
// Logical segment - the portion of the strip effects actually run on.
// (Set SKIP=0 and RING_LEN=NUM_LEDS if you want to use the whole strip.)
// ---------------------------------------------------------------------------
#define SKIP          0
#define RING_LEN      70

// ---------------------------------------------------------------------------
// Defaults (used on first boot, and after a factory reset)
// ---------------------------------------------------------------------------
#define DEFAULT_BRIGHTNESS   80
#define DEFAULT_SPEED        20     // ms between animation frames, lower = faster
#define DEFAULT_EFFECT       0      // 0 = Solid

// ---------------------------------------------------------------------------
// Networking
// ---------------------------------------------------------------------------
// WiFi is optional: USB serial control always works. With WIFI_ENABLED 1 the
// board connects in the background; if it can't, it keeps running serial-only
// and retries every WIFI_RETRY_INTERVAL_S.
#define WIFI_ENABLED  1
#define HOSTNAME      "led-controller"        // reachable at http://led-controller.local
#define HTTP_PORT     80

// Hardcoded WiFi credentials (fill these in; don't commit real values to a public repo)
#define WIFI_SSID     "SmarterPrinterAP"
#define WIFI_PASS     "smarter1"
#define WIFI_CONNECT_TIMEOUT_S  30            // per attempt, before falling back to serial-only
#define WIFI_RETRY_INTERVAL_S   60            // how long to wait between connect attempts
