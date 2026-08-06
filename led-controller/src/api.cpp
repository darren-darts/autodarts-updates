#include "api.h"
#include <ArduinoJson.h>   // v7.x API
#include <WiFi.h>
#include "config.h"
#include "state_json.h"

static void sendCors(WebServer &server) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
}

static void sendJson(WebServer &server, int code, JsonDocument &doc) {
  String out;
  serializeJson(doc, out);
  sendCors(server);
  server.send(code, "application/json", out);
}

static void sendError(WebServer &server, int code, const char *msg) {
  JsonDocument doc;
  doc["error"] = msg;
  sendJson(server, code, doc);
}

void api_setup(WebServer &server, LedState &state) {

  // --- GET /api/state --------------------------------------------------
  server.on("/api/state", HTTP_GET, [&server, &state]() {
    JsonDocument doc;
    stateToJson(state, doc);
    sendJson(server, 200, doc);
  });

  // --- POST /api/state (JSON body, partial updates allowed) ------------
  server.on("/api/state", HTTP_POST, [&server, &state]() {
    if (!server.hasArg("plain")) { sendError(server, 400, "missing JSON body"); return; }
    JsonDocument in;
    DeserializationError err = deserializeJson(in, server.arg("plain"));
    if (err) { sendError(server, 400, "invalid JSON"); return; }

    String errMsg;
    if (!applyJsonToState(in.as<JsonVariantConst>(), state, errMsg)) {
      sendError(server, 400, errMsg.c_str());
      return;
    }

    JsonDocument out;
    stateToJson(state, out);
    sendJson(server, 200, out);
  });

  // --- GET /api/set?on=1&bri=128&fx=2&sx=20&r=255&g=0&b=0&r2=&g2=&b2= ---
  // Convenience endpoint for quick testing from a browser address bar.
  server.on("/api/set", HTTP_GET, [&server, &state]() {
    if (server.hasArg("on"))  state.on = server.arg("on").toInt() != 0;
    if (server.hasArg("bri")) state.brightness = constrain(server.arg("bri").toInt(), 0, 255);
    if (server.hasArg("fx"))  state.effect = constrain(server.arg("fx").toInt(), 0, FX_COUNT - 1);
    if (server.hasArg("sx"))  state.speed = constrain(server.arg("sx").toInt(), 0, 5000);
    if (server.hasArg("r") && server.hasArg("g") && server.hasArg("b")) {
      state.color = CRGB(server.arg("r").toInt(), server.arg("g").toInt(), server.arg("b").toInt());
    }
    if (server.hasArg("r2") && server.hasArg("g2") && server.hasArg("b2")) {
      state.color2 = CRGB(server.arg("r2").toInt(), server.arg("g2").toInt(), server.arg("b2").toInt());
    }
    if (server.hasArg("skip") || server.hasArg("len")) {
      int skip = server.hasArg("skip") ? constrain(server.arg("skip").toInt(), 0, NUM_LEDS - 1) : effects_skip();
      int len  = server.hasArg("len")  ? constrain(server.arg("len").toInt(), 1, NUM_LEDS)      : effects_ringLen();
      effects_setSegment(skip, len);
    }
    JsonDocument out;
    stateToJson(state, out);
    sendJson(server, 200, out);
  });

  // --- GET /api/effects --------------------------------------------------
  server.on("/api/effects", HTTP_GET, [&server]() {
    JsonDocument doc;
    JsonArray arr = doc["effects"].to<JsonArray>();
    for (int i = 0; i < FX_COUNT; i++) {
      JsonObject o = arr.add<JsonObject>();
      o["id"] = i;
      o["name"] = effectNames[i];
    }
    doc["count"] = FX_COUNT;
    sendJson(server, 200, doc);
  });

  // --- GET /api/info ------------------------------------------------------
  server.on("/api/info", HTTP_GET, [&server]() {
    JsonDocument doc;
    doc["name"] = HOSTNAME;
    doc["ip"] = WiFi.localIP().toString();
    doc["mac"] = WiFi.macAddress();
    doc["uptimeMs"] = millis();
    doc["numLeds"] = NUM_LEDS;
    doc["skip"] = effects_skip();
    doc["ringLen"] = effects_ringLen();
    doc["freeHeap"] = ESP.getFreeHeap();
    sendJson(server, 200, doc);
  });

  // --- POST /api/reset  (wipe WiFi credentials + reboot into portal) -----
  server.on("/api/reset", HTTP_POST, [&server]() {
    JsonDocument doc;
    doc["status"] = "resetting";
    sendJson(server, 200, doc);
    delay(200);
    ESP.restart();
  });

  // --- CORS preflight for all routes --------------------------------------
  server.onNotFound([&server]() {
    if (server.method() == HTTP_OPTIONS) {
      sendCors(server);
      server.send(204);
    } else {
      sendError(server, 404, "not found");
    }
  });

  server.begin();
}
