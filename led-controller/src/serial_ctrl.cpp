#include "serial_ctrl.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>

#include "config.h"
#include "state_json.h"

static String rxBuf;

static void reply(JsonDocument &doc) {
  serializeJson(doc, Serial);
  Serial.println();
}

static void replyError(const char *msg) {
  JsonDocument doc;
  doc["error"] = msg;
  reply(doc);
}

static void handleLine(const String &line, LedState &state) {
  JsonDocument in;
  if (deserializeJson(in, line)) {
    replyError("invalid JSON");
    return;
  }

  const char *cmd = in["cmd"] | "";

  if (strcmp(cmd, "ping") == 0) {
    JsonDocument doc;
    doc["pong"] = true;
    reply(doc);
    return;
  }

  if (strcmp(cmd, "state") == 0) {
    JsonDocument doc;
    stateToJson(state, doc);
    reply(doc);
    return;
  }

  if (strcmp(cmd, "effects") == 0) {
    JsonDocument doc;
    JsonArray arr = doc["effects"].to<JsonArray>();
    for (int i = 0; i < FX_COUNT; i++) {
      JsonObject o = arr.add<JsonObject>();
      o["id"] = i;
      o["name"] = effectNames[i];
    }
    doc["count"] = FX_COUNT;
    reply(doc);
    return;
  }

  if (strcmp(cmd, "info") == 0) {
    JsonDocument doc;
    doc["name"] = HOSTNAME;
    doc["wifi"] = WiFi.status() == WL_CONNECTED;
    doc["ip"] = WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "";
    doc["mac"] = WiFi.macAddress();
    doc["uptimeMs"] = millis();
    doc["numLeds"] = NUM_LEDS;
    doc["skip"] = effects_skip();
    doc["ringLen"] = effects_ringLen();
    doc["freeHeap"] = ESP.getFreeHeap();
    reply(doc);
    return;
  }

  if (cmd[0] != '\0') {
    replyError("unknown cmd");
    return;
  }

  // No "cmd": treat the object as a partial state update.
  String errMsg;
  if (!applyJsonToState(in.as<JsonVariantConst>(), state, errMsg)) {
    replyError(errMsg.c_str());
    return;
  }
  JsonDocument doc;
  stateToJson(state, doc);
  reply(doc);
}

void serial_ctrl_poll(LedState &state) {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      rxBuf.trim();
      if (rxBuf.length() > 0) handleLine(rxBuf, state);
      rxBuf = "";
    } else if (c != '\r') {
      rxBuf += c;
      if (rxBuf.length() > 1024) rxBuf = "";  // discard runaway input
    }
  }
}
