#pragma once
#include <ArduinoJson.h>
#include "effects.h"

// Shared JSON <-> LedState conversion, used by both the HTTP API (api.cpp)
// and the USB serial protocol (serial_ctrl.cpp) so the two speak an
// identical schema.

void stateToJson(LedState &state, JsonDocument &doc);

// Applies any fields present in a JSON object to state. Returns false + sets
// errMsg if a value was out of range or the wrong type.
bool applyJsonToState(JsonVariantConst obj, LedState &state, String &errMsg);
