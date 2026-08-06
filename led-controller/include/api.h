#pragma once
#include <WebServer.h>
#include "effects.h"

// Wires up all /api/* routes on the given server, operating on the given
// shared LedState. Call once from setup(), after WiFi is connected.
void api_setup(WebServer &server, LedState &state);
