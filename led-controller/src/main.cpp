#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <FastLED.h>

#include "config.h"
#include "effects.h"
#include "api.h"
#include "serial_ctrl.h"

CRGB leds[NUM_LEDS];
LedState state;
WebServer server(HTTP_PORT);

// WiFi is optional: serial control works from the moment the board boots,
// and the network comes up in the background if it can. Log lines are
// prefixed with "# " so they never collide with serial protocol replies.
enum WifiPhase { WIFI_DISABLED, WIFI_CONNECTING, WIFI_UP, WIFI_RETRY_WAIT };
static WifiPhase wifiPhase = WIFI_DISABLED;
static uint32_t wifiPhaseStart = 0;
static bool servicesStarted = false;

static void wifiStartConnect() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  wifiPhase = WIFI_CONNECTING;
  wifiPhaseStart = millis();
  Serial.printf("# WiFi: connecting to %s\n", WIFI_SSID);
}

static void wifiStartServices() {
  if (MDNS.begin(HOSTNAME)) {
    MDNS.addService("http", "tcp", HTTP_PORT);
    Serial.printf("# mDNS started: http://%s.local\n", HOSTNAME);
  }

  // OTA updates: pio run -e esp32ota -t upload
  ArduinoOTA.setHostname(HOSTNAME);
  ArduinoOTA.onStart([]() {
    FastLED.clear(true);   // stop driving LEDs during the update
  });
  ArduinoOTA.begin();

  if (!servicesStarted) {
    api_setup(server, state);   // registers routes + server.begin()
    servicesStarted = true;
  }
  Serial.print("# WiFi connected, IP: ");
  Serial.println(WiFi.localIP());
}

static void wifiTick() {
  switch (wifiPhase) {
    case WIFI_DISABLED:
      break;

    case WIFI_CONNECTING:
      if (WiFi.status() == WL_CONNECTED) {
        wifiStartServices();
        wifiPhase = WIFI_UP;
      } else if (millis() - wifiPhaseStart > WIFI_CONNECT_TIMEOUT_S * 1000UL) {
        Serial.println("# WiFi: connect timed out, continuing serial-only");
        WiFi.disconnect(true);
        wifiPhase = WIFI_RETRY_WAIT;
        wifiPhaseStart = millis();
      }
      break;

    case WIFI_UP:
      ArduinoOTA.handle();
      server.handleClient();
      if (WiFi.status() != WL_CONNECTED) {
        // The WiFi stack auto-reconnects; just note it and keep serving
        // serial in the meantime.
        Serial.println("# WiFi: connection lost, waiting for reconnect");
        wifiPhase = WIFI_CONNECTING;
        wifiPhaseStart = millis();
      }
      break;

    case WIFI_RETRY_WAIT:
      if (millis() - wifiPhaseStart > WIFI_RETRY_INTERVAL_S * 1000UL) {
        wifiStartConnect();
      }
      break;
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n# LED Controller booting...");

  // ---- LEDs: ready before anything else ----------------------------------
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(DEFAULT_BRIGHTNESS);
  FastLED.clear(true);
  effects_init(leds, NUM_LEDS, SKIP, RING_LEN);

  state.brightness = DEFAULT_BRIGHTNESS;
  state.speed = DEFAULT_SPEED;
  state.effect = DEFAULT_EFFECT;

  // Brief green flash to confirm boot succeeded, then hand off to effects.
  fill_solid(leds + SKIP, RING_LEN, CRGB::Green);
  FastLED.show();
  delay(300);
  FastLED.clear(true);

  Serial.println("# Serial control ready (JSON lines, see serial_ctrl.h)");

#if WIFI_ENABLED
  wifiStartConnect();
#else
  Serial.println("# WiFi disabled in config.h, serial-only mode");
#endif
}

void loop() {
  serial_ctrl_poll(state);
  wifiTick();
  effects_run(state);
}
