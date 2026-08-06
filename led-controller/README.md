# LED Controller (custom mini-WLED)

A standalone ESP32 + WS2812B controller: WiFi captive portal setup, 22 built-in
effects, and a REST API for full external control.

## Hardware

- ESP32 dev board
- WS2812B strip, data wired to **GPIO 5** (`include/config.h`)
- 5V power supply sized for your LED count, common ground with the ESP32
- (Recommended) 74AHCT125 level shifter on the data line — ESP32 is 3.3V logic

## Project layout

```
led-controller/
├── platformio.ini
├── include/
│   ├── config.h      # hardware pins, LED counts, WiFi hostname/AP name
│   ├── effects.h      # effect list + shared state struct
│   └── webserver.h
└── src/
    ├── main.cpp        # boot, WiFi portal, main loop
    ├── effects.cpp     # 22 effect implementations
    └── webserver.cpp   # REST API routes
```

## First-time setup

1. Edit `include/config.h`:
   - `DATA_PIN`, `NUM_LEDS` — match your actual wiring/strip length
   - `SKIP` / `RING_LEN` — the segment of the strip effects run on (currently 13/66,
     matching your ring)
2. Build and flash with PlatformIO:
   ```
   pio run -t upload
   pio device monitor
   ```
3. On first boot (or after a factory reset), the ESP32 opens a WiFi access point
   called **`LED-Controller-Setup`** (no password). Connect to it from your
   phone/laptop — a captive portal should pop up automatically (or open
   `192.168.4.1` in a browser). Pick your home WiFi network and enter its
   password.
4. The ring flashes dim purple while the portal is open, then green briefly once
   it connects successfully.
5. Once connected, the device is reachable at `http://led-controller.local`
   (or check the serial monitor / your router for its IP).

To re-open the setup portal later (e.g. to switch WiFi networks), call
`POST /api/reset` — this wipes saved credentials and reboots into the portal.

## REST API

All endpoints return JSON. Base URL: `http://led-controller.local` (or the
device's IP).

### `GET /api/state`
Returns the current full state.
```json
{"on":true,"bri":80,"fx":2,"fxName":"Rainbow Cycle","sx":20,"col":[255,255,255],"col2":[0,0,0]}
```

### `POST /api/state`
Partial update — send only the fields you want to change.
```bash
curl -X POST http://led-controller.local/api/state \
  -H "Content-Type: application/json" \
  -d '{"on":true,"fx":14,"col":[255,0,0],"bri":150,"sx":15}'
```
Fields:
| Field | Type | Range | Meaning |
|---|---|---|---|
| `on` | bool | — | power on/off |
| `bri` | int | 0-255 | brightness |
| `fx` | int | 0-21 | effect id, see `/api/effects` |
| `sx` | int | 0-5000 | ms between animation frames (lower = faster) |
| `col` | [r,g,b] | 0-255 each | primary color |
| `col2` | [r,g,b] | 0-255 each | secondary color (used by some effects) |

### `GET /api/set`
Same fields as above, but as query params — handy for testing from a browser
address bar, e.g.:
```
http://led-controller.local/api/set?on=1&fx=14&r=255&g=0&b=0&bri=150&sx=15
```

### `GET /api/effects`
Lists all available effects and their ids.

### `GET /api/info`
Device info: IP, MAC, uptime, free heap, LED counts.

### `POST /api/reset`
Wipes saved WiFi credentials and reboots into the setup portal.

## Effects (ids 0-21)

| id | Name | | id | Name |
|---|---|---|---|---|
| 0 | Solid | | 11 | Breathe |
| 1 | Rainbow | | 12 | Strobe |
| 2 | Rainbow Cycle | | 13 | Fire |
| 3 | Theater Chase | | 14 | Comet |
| 4 | Theater Chase Rainbow | | 15 | Meteor |
| 5 | Color Wipe | | 16 | Running Lights |
| 6 | Color Wipe Random | | 17 | Confetti |
| 7 | Scan | | 18 | Juggle |
| 8 | Dual Scan | | 19 | BPM |
| 9 | Twinkle | | 20 | Fade |
| 10 | Sparkle | | 21 | Chase Rainbow |

## Notes / things worth knowing

- The main loop is fully non-blocking (no `delay()` in `loop()`), so the API
  stays responsive while an effect is animating.
- `SKIP`/`RING_LEN` are compile-time constants, not exposed via the API yet —
  edit `config.h` and reflash if your physical segment changes.
- If effects glitch (random pixels, flicker) at high brightness/full white,
  it's almost always the PSU or the missing level shifter, not the code —
  see the wiring notes above.
