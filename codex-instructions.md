# InterDarts Rearrangement: Replace Custom Camera/Detection Stack with Autodarts

## Objective

Refactor the InterDarts project so that **Autodarts becomes the sole camera, calibration, dart-detection and dart-localisation engine**.

The existing Python code for:

* camera discovery
* camera capture
* camera streaming used for scoring
* camera calibration
* board geometry/calibration
* motion detection
* dart detection
* dart localisation
* dart-to-segment calculation

should be removed from the active application architecture wherever it is no longer required.

InterDarts should instead consume detected dart information from the local Autodarts Board Manager API.

Autodarts exposes:

```text
http://127.0.0.1:3180/api/state
```

This has been tested successfully on both:

* Raspberry Pi
* Windows

The InterDarts application should therefore use the same detection integration on both platforms.

---

# Target Architecture

The desired architecture is:

```text
3 USB Cameras
      |
      v
AUTODARTS
- camera management
- calibration
- motion detection
- dart detection
- dart localisation
- board segment recognition
      |
      | http://127.0.0.1:3180/api/state
      v
AutodartsDetector
      |
      v
Normalised Dart Events
      |
      v
Existing InterDarts Game Engine
      |
      +---- Vue GUI
      |
      +---- LED effects / ESP32
      |
      +---- sounds
      |
      +---- game state
```

Autodarts must be treated as an external detection service.

Do NOT modify or patch Autodarts itself.

---

# Important Design Rule

The rest of InterDarts must NOT depend directly on Autodarts JSON structures.

Create a clean abstraction between Autodarts and the existing game engine.

For example:

```text
Autodarts
    |
    v
AutodartsDetector
    |
    v
DartEvent
    |
    v
Game Engine
```

This ensures that another detector could theoretically be substituted later without rewriting the games.

---

# Create a Detector Abstraction

Introduce a detector interface/base class if an equivalent abstraction does not already exist.

Conceptually:

```python
class DartDetector:
    async def start(self):
        pass

    async def stop(self):
        pass
```

The Autodarts implementation should be:

```python
class AutodartsDetector(DartDetector):
    ...
```

The implementation may use:

```python
httpx.AsyncClient
```

and poll:

```text
http://127.0.0.1:3180/api/state
```

approximately every:

```text
100 ms
```

Do not block the FastAPI event loop.

---

# Autodarts State Format

Typical Autodarts state:

```json
{
  "connected": true,
  "running": true,
  "status": "Throw",
  "event": "Throw detected",
  "numThrows": 2,
  "throws": [
    {
      "segment": {
        "name": "S20",
        "number": 20,
        "bed": "SingleOuter",
        "multiplier": 1
      },
      "coords": {
        "x": -0.087,
        "y": 0.762
      }
    }
  ]
}
```

Example triple:

```json
{
  "segment": {
    "name": "T3",
    "number": 3,
    "bed": "Triple",
    "multiplier": 3
  }
}
```

Example outer bull:

```json
{
  "segment": {
    "name": "25",
    "number": 25,
    "bed": "Single",
    "multiplier": 1
  }
}
```

Do NOT assume all segment names start with S, D or T.

Bull segments must be handled correctly.

---

# Normalised Dart Event

Convert the Autodarts representation into an internal InterDarts model.

For example:

```python
@dataclass
class DartEvent:
    segment: str
    number: int
    multiplier: int
    score: int
    bed: str
    x: float
    y: float
    dart_number: int
```

Or use the project's existing Pydantic/domain modelling conventions.

Example event:

```json
{
  "type": "dart_thrown",
  "segment": "T20",
  "number": 20,
  "multiplier": 3,
  "score": 60,
  "bed": "Triple",
  "x": 0.123,
  "y": -0.456,
  "dart_number": 2
}
```

Score should normally be calculated as:

```python
number * multiplier
```

but correctly preserve Autodarts bull behaviour.

---

# Visit / Takeout Lifecycle

Autodarts also provides reliable visit lifecycle information.

Observed states include:

```text
event = "Throw detected"

event = "Takeout started"

event = "Takeout finished"
```

After darts have been removed:

```json
{
  "status": "Throw",
  "event": "Takeout finished",
  "numThrows": 0
}
```

Expose these internally as events such as:

```json
{
  "type": "takeout_started"
}
```

and:

```json
{
  "type": "takeout_finished"
}
```

The game engine can then decide what these mean for the current game.

For example:

```text
takeout_finished
       |
       +-- next player
       +-- next round
       +-- reset visit
       +-- GUI update
```

Do NOT hard-code player switching into the Autodarts adapter.

The adapter detects physical board events only.

The game engine owns game rules.

---

# Avoid Duplicate Events

The Autodarts API returns the current state rather than an event stream.

Therefore polling must not generate duplicate throws.

Track state internally.

A throw should only be emitted when a new throw appears.

Typical logic:

```text
Previous:
numThrows = 1

Current:
event = Throw detected
numThrows = 2

=> emit dart 2
```

During dart removal, `numThrows` may decrease:

```text
3
2
1
0
```

This must NOT be interpreted as new dart events.

Use an internal state machine rather than treating every change in `numThrows` as a throw.

Suggested internal state:

```python
last_throw_count
visit_active
takeout_active
```

Only emit throw events for increasing throw counts associated with:

```text
event == "Throw detected"
```

---

# Handle Startup State Correctly

Autodarts may retain its previous event.

For example, when InterDarts starts, `/api/state` may initially contain:

```text
event = "Takeout finished"
```

Do NOT immediately generate a false:

```text
VISIT COMPLETE
```

Only accept takeout lifecycle events after the adapter has observed an active visit during the current lifecycle.

---

# FastAPI Integration

The detector should run as a background async task managed by the FastAPI application lifecycle.

Preferred structure:

```text
FastAPI starts
      |
      v
AutodartsDetector.start()
      |
      v
background polling task
```

On FastAPI shutdown:

```text
AutodartsDetector.stop()
```

Do not create uncontrolled background threads if an asyncio task is sufficient.

---

# Event Delivery

Do not tightly couple `AutodartsDetector` to individual games.

Use one of the project's existing event mechanisms if suitable.

Good approaches include:

```text
asyncio.Queue
```

or an application-level event bus/callback interface.

For example:

```python
dart_event_queue: asyncio.Queue
```

Detector:

```text
AutodartsDetector
        |
        v
dart_event_queue
        |
        v
game controller
```

The game layer consumes generic dart events.

---

# Vue / WebSocket Integration

Continue using the existing FastAPI-to-Vue communication architecture wherever possible.

The Vue application should receive normalised InterDarts events, not raw Autodarts JSON.

For example:

```json
{
  "type": "dart_thrown",
  "segment": "D16",
  "number": 16,
  "multiplier": 2,
  "score": 32,
  "dart_number": 1,
  "coords": {
    "x": 0.12,
    "y": -0.38
  }
}
```

This allows the GUI to show:

* detected segment
* score
* dart number
* dart impact position
* game-specific animations

without knowing anything about Autodarts.

---

# Coordinates

Preserve the Autodarts:

```text
coords.x
coords.y
```

values.

These may be useful later for:

* displaying actual dart positions on the Vue dartboard
* animations
* heat maps
* accuracy statistics
* special games based on exact location

Do not discard them even if the current games do not use them.

---

# Camera and Calibration UI

Review the current Vue application for screens/components associated with:

* camera selection
* camera preview specifically used for scoring setup
* camera calibration
* calibration points
* board geometry
* camera exposure/settings used by the old detector
* detection debugging

Remove or disable these if they are exclusively part of the old InterDarts detection engine.

Do NOT remove unrelated camera functionality without first checking its purpose.

For example, if cameras are used separately for:

* selfies
* player photos
* video features

those features should remain.

Autodarts only replaces cameras used for automatic dart detection.

---

# Autodarts Configuration

InterDarts should NOT recreate the Autodarts calibration interface.

Camera configuration and calibration should remain the responsibility of Autodarts Board Manager.

The user can access it at:

```text
http://localhost:3180
```

or from another machine:

```text
http://<device-ip>:3180
```

InterDarts may later provide a button such as:

```text
Configure Dart Cameras
```

which opens the Autodarts Board Manager, but do not duplicate its calibration functionality.

---

# Autodarts Health Monitoring

Add a simple health/status mechanism.

The application should determine:

```text
Autodarts reachable?
Autodarts connected?
Autodarts running?
```

using `/api/state`.

Possible internal status:

```json
{
  "detector": "autodarts",
  "available": true,
  "connected": true,
  "running": true
}
```

If Autodarts is unavailable, InterDarts should remain running and report a clear error rather than crash.

For example:

```text
Autodarts detection service unavailable.
Start or configure Autodarts before playing.
```

Retry connection automatically.

---

# Cross-Platform Requirement

The same Python implementation must work on:

```text
Windows
Raspberry Pi / Linux
```

because both expose:

```text
http://127.0.0.1:3180/api/state
```

Do not add Windows-specific or Raspberry-Pi-specific detection logic unless absolutely necessary.

Autodarts startup itself may differ by OS, but the InterDarts integration should remain platform independent.

---

# Dependencies

Add:

```text
httpx
```

to the appropriate backend `requirements.txt` if it is not already present.

Do not introduce unnecessary dependencies.

---

# Remove Obsolete Python Code Carefully

Identify all old camera/calibration/detection code before deleting anything.

Categorise it as:

```text
REMOVE
RETAIN
REFACTOR
UNKNOWN
```

Remove code only if it belongs exclusively to the old automatic dart-detection stack.

Likely candidates for removal include:

```text
camera capture for dart detection
camera synchronisation
dart motion detection
dart image comparison
board calibration mathematics
segment lookup from image coordinates
camera calibration persistence
dart CV processing
OpenCV scoring pipeline
```

Retain anything still required elsewhere.

Before deleting a module, search the complete repository for imports and references.

---

# Remove Obsolete Configuration

Review:

* configuration files
* environment variables
* database fields
* backend settings
* Vue settings
* startup scripts

for values associated exclusively with the old detector.

Examples might include:

```text
camera IDs
camera exposure
camera resolution
calibration coordinates
board geometry
CV thresholds
motion thresholds
detection sensitivity
```

Remove obsolete configuration only where it is no longer referenced.

Avoid unnecessary database migrations unless fields are clearly obsolete and safe to remove.

---

# Preserve Existing Game Logic

Do NOT rewrite the individual games unless necessary.

The purpose of this refactor is to change:

```text
HOW a dart is detected
```

not:

```text
HOW the games work
```

Adapt the detector output to the interfaces already expected by the game engine wherever practical.

Existing games, player management, scoring logic, sounds, LED control and Vue layouts should remain operational.

---

# Recommended File Structure

Adapt to the existing project conventions, but a structure similar to this would be appropriate:

```text
backend/
    detection/
        __init__.py
        base.py
        autodarts.py
        models.py
```

For example:

```text
base.py
    DartDetector

models.py
    DartEvent
    TakeoutStartedEvent
    TakeoutFinishedEvent

autodarts.py
    AutodartsDetector
```

Do not create this exact structure if equivalent abstractions already exist.

Prefer integrating cleanly into the current architecture.

---

# Logging

Add useful but restrained logging.

Examples:

```text
Autodarts detector connected
Autodarts detector disconnected

Dart detected: T20 = 60
Dart detected: S5 = 5

Takeout started
Takeout finished
```

Avoid logging the complete `/api/state` response ten times per second.

Debug-level logging may expose raw states if needed.

---

# Error Handling

Handle at least:

```text
connection refused
HTTP timeout
invalid JSON
Autodarts stopped
Autodarts running=false
missing throws
unexpected segment format
```

The polling loop must survive transient failures.

Example behaviour:

```text
Autodarts goes offline
        |
        v
log warning
        |
        v
continue retrying
        |
        v
Autodarts returns
        |
        v
resume detection
```

Do not terminate FastAPI because Autodarts is temporarily unavailable.

---

# Testing

Add unit tests for the Autodarts state parser.

Use stored example states representing:

## Single

```json
{
  "event": "Throw detected",
  "numThrows": 1,
  "throws": [
    {
      "segment": {
        "name": "S20",
        "number": 20,
        "bed": "SingleOuter",
        "multiplier": 1
      },
      "coords": {
        "x": -0.087,
        "y": 0.762
      }
    }
  ]
}
```

Expected:

```text
S20
score 20
```

## Triple

```text
T3
number 3
multiplier 3
```

Expected:

```text
score 9
```

## Bull

```text
name = "25"
number = 25
multiplier = 1
```

Expected:

```text
score 25
```

## Visit lifecycle

Test:

```text
Throw 1
Throw 2
Throw 3
Takeout started
Takeout finished
numThrows = 0
```

Confirm:

```text
exactly 3 dart events
exactly 1 takeout_started
exactly 1 takeout_finished
```

Confirm polling identical states does NOT create duplicate dart events.

---

# Migration Strategy

Perform the work in this order:

```text
1. Analyse existing camera/detection architecture.

2. Identify interfaces currently used by the game engine.

3. Implement the generic detector abstraction.

4. Implement AutodartsDetector.

5. Feed Autodarts events into the existing game engine.

6. Verify existing games receive correct dart events.

7. Verify Windows.

8. Verify Raspberry Pi.

9. Remove obsolete scoring camera/detection code.

10. Remove obsolete GUI/configuration.

11. Run complete regression tests.

12. Clean imports, dependencies and dead files.
```

Do NOT begin by deleting the existing detector.

First get Autodarts feeding the existing game engine successfully.

Then remove the obsolete implementation.

---

# Acceptance Criteria

The refactor is complete when:

1. InterDarts no longer performs its own camera-based dart detection.

2. Autodarts is responsible for:

```text
camera handling
camera calibration
motion detection
dart localisation
segment detection
```

3. InterDarts receives detected darts through:

```text
http://127.0.0.1:3180/api/state
```

4. Three-dart visits work correctly.

5. Singles, doubles, triples and bulls are correctly represented.

6. Takeout start and finish are correctly detected.

7. Duplicate polling responses do not create duplicate darts.

8. Autodarts being unavailable does not crash InterDarts.

9. The same integration works on Windows and Raspberry Pi.

10. Existing games continue to work without needing Autodarts-specific code.

11. Existing Vue game screens continue to receive dart events through the normal InterDarts backend/WebSocket architecture.

12. Old camera/calibration/CV detection functionality has been removed where it is no longer required.

13. Unrelated camera functionality has NOT been removed accidentally.

14. The project starts cleanly with no dead imports or references to removed detector modules.

---

# Important Constraint

Do not redesign the entire InterDarts application.

This should be a focused architectural replacement:

```text
OLD

Cameras
  ->
InterDarts Python CV/calibration/detection
  ->
Game Engine


NEW

Cameras
  ->
Autodarts
  ->
AutodartsDetector
  ->
Game Engine
```

Preserve the existing game engine, FastAPI architecture, Vue application, LED integration and other working functionality wherever possible.

Before making changes, inspect the repository and identify the existing detection entry points and downstream consumers. Use those existing interfaces where practical rather than introducing unnecessary parallel architecture.
