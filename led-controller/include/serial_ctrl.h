#pragma once
#include "effects.h"

// USB serial control protocol: one JSON object per line at 115200 baud.
//
//   {"fx": 32, "col": [255,0,0]}   -> partial state update (same schema as
//                                     POST /api/state), replies with full state
//   {"cmd": "state"}               -> replies with full state
//   {"cmd": "effects"}             -> replies with the effect list
//   {"cmd": "info"}                -> replies with device/network info
//   {"cmd": "ping"}                -> replies {"pong":true}
//
// Every reply is a single JSON line. Log output is prefixed with "# " so a
// host can parse replies by only reading lines that start with '{'.

void serial_ctrl_poll(LedState &state);
