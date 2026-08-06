#include "state_json.h"
#include "config.h"

void stateToJson(LedState &state, JsonDocument &doc) {
  doc["on"] = state.on;
  doc["bri"] = state.brightness;
  doc["fx"] = state.effect;
  doc["fxName"] = effectNames[state.effect];
  doc["sx"] = state.speed;
  doc["skip"] = effects_skip();
  doc["len"] = effects_ringLen();

  JsonArray col = doc["col"].to<JsonArray>();
  col.add(state.color.r); col.add(state.color.g); col.add(state.color.b);

  JsonArray col2 = doc["col2"].to<JsonArray>();
  col2.add(state.color2.r); col2.add(state.color2.g); col2.add(state.color2.b);
}

bool applyJsonToState(JsonVariantConst obj, LedState &state, String &errMsg) {
  if (!obj["on"].isNull()) {
    if (!obj["on"].is<bool>()) { errMsg = "'on' must be boolean"; return false; }
    state.on = obj["on"];
  }
  if (!obj["bri"].isNull()) {
    int v = obj["bri"];
    if (v < 0 || v > 255) { errMsg = "'bri' must be 0-255"; return false; }
    state.brightness = v;
  }
  if (!obj["fx"].isNull()) {
    int v = obj["fx"];
    if (v < 0 || v >= FX_COUNT) { errMsg = "'fx' out of range, see effects list"; return false; }
    state.effect = v;
  }
  if (!obj["sx"].isNull()) {
    int v = obj["sx"];
    if (v < 0 || v > 5000) { errMsg = "'sx' must be 0-5000 (ms)"; return false; }
    state.speed = v;
  }
  if (!obj["col"].isNull()) {
    JsonArrayConst c = obj["col"];
    if (c.size() != 3) { errMsg = "'col' must be [r,g,b]"; return false; }
    state.color = CRGB(c[0], c[1], c[2]);
  }
  if (!obj["col2"].isNull()) {
    JsonArrayConst c = obj["col2"];
    if (c.size() != 3) { errMsg = "'col2' must be [r,g,b]"; return false; }
    state.color2 = CRGB(c[0], c[1], c[2]);
  }
  if (!obj["skip"].isNull() || !obj["len"].isNull()) {
    int skip = obj["skip"].isNull() ? effects_skip()    : obj["skip"].as<int>();
    int len  = obj["len"].isNull()  ? effects_ringLen() : obj["len"].as<int>();
    if (skip < 0 || skip >= NUM_LEDS) { errMsg = "'skip' must be 0-" + String(NUM_LEDS - 1); return false; }
    if (len < 1 || skip + len > NUM_LEDS) { errMsg = "'len' must be 1-" + String(NUM_LEDS) + " and skip+len <= " + String(NUM_LEDS); return false; }
    effects_setSegment(skip, len);
  }
  return true;
}
