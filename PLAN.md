# Interactive Darts Application — Architecture & Plan

## Goals

- Automatic dart detection using 3 USB cameras
- Library of fun games including standard x01, all built on one core engine
- Multiple players, with phone-friendly join flow (QR code, selfie avatars)
- Runs identically on Raspberry Pi 5 and Windows PC
- LED effects via ESP32, controllable over USB serial and/or WiFi
- Robust, layered architecture where each part can be tuned independently

## Headline decisions

| Question | Recommendation | Why |
|---|---|---|
| Backend | Python + FastAPI | You need Python for OpenCV anyway; FastAPI gives async WebSockets, REST, and static file serving in one process |
| Web GUI | Vue 3 + Vite (SPA served by FastAPI) | "Fancy graphics" and live score updates need a real JS front end; Python-rendered HTML (Jinja/HTMX) can't deliver animated game screens well |
| Detection ↔ UI link | WebSocket event stream | Darts landing are *events*; every UI (main, phone) subscribes to the same stream |
| Game engine | Event-sourced state machine + game registry | Undo/correction falls out naturally; new games are plugins |
| Detection tuning | Offline "dart lab" replay harness | Record camera footage once, tune algorithms without standing at the board |

### Why Vue and not Python for the GUI

The backend stays 100% Python. The question is only how the browser pages are built:

- **Python-rendered (Jinja2 + HTMX):** fine for forms and settings, weak for animated scoreboards, checkout suggestions flying in, per-game themed graphics, and sub-100ms score updates.
- **Vue 3 SPA:** components map cleanly onto your needs — one shared core (WebSocket client, player store) with a desktop shell and a phone shell as two routes/layouts. Built files are static assets FastAPI serves, so deployment on the Pi is just copying `dist/`.

Vue 3 + Vite + Pinia (state) is the sweet spot. No Node needed at runtime — only at build time, and you can build on the PC and deploy static files to the Pi.

## System architecture

```
┌────────────────────────────────────────────────────────────────┐
│                        FastAPI process                         │
│                                                                │
│  Capture layer      Detection pipeline      Game engine        │
│  ┌──────────┐       ┌───────────────┐      ┌──────────────┐    │
│  │ 3 x USB  │ frames│ motion detect │ hits │ x01, cricket │    │
│  │ cameras  ├──────►│ tip locate    ├─────►│ shanghai ... │    │
│  │ (threads)│       │ triangulate   │      │ (registry)   │    │
│  └──────────┘       │ score map     │      └──────┬───────┘    │
│        ▲            └───────┬───────┘             │ events     │
│        │                    │                     ▼            │
│  Calibration store          │              Event bus (async)   │
│  (per-camera JSON)◄─────────┘                     │            │
│                                    ┌──────────────┼─────────┐  │
│                                    ▼              ▼         ▼  │
│                              WebSocket hub  led_controller REST│
└────────────────────────────────────┬──────────────┬─────────┬──┘
                                     │              │         │
                     ┌───────────────┴───┐   ┌──────▼─────┐   │
                     │ Main GUI (Vue)    │   │ ESP32      │   │
                     │ Phone GUI (Vue)   │   │ USB serial │   │
                     └───────────────────┘   │  or WiFi   │   │
                                             setup/config ◄───┘
```

Key principle: **layers only talk through defined interfaces**, so each can be swapped or tuned:

1. **Capture** — one thread per camera grabbing frames into ring buffers. Camera backends differ (V4L2 on Pi, DirectShow/MSMF on Windows) but `cv2.VideoCapture` + a small platform config (device index/path, resolution, fps, exposure) hides that.
2. **Detection** — consumes frames, emits `DartDetected(tip_xy_board, segment, ring, confidence, camera_votes)` events. Knows nothing about games.
3. **Game engine** — consumes scoring events, emits game-state events. Knows nothing about cameras.
4. **Delivery** — WebSocket hub broadcasts game/detection events to all connected UIs; the `led_controller` hook (see below) translates game/calibration events into ESP32 effect calls.

## Calibration & detection (the fundamental part — agreed)

### Calibration — built

Per-camera **homography** from image plane to board-space millimetres, exactly as planned above. Auto-detect first, manual correction always available — never blocks on auto-detection failing.

- **`backend/calibration/board_model.py`** — pure geometry, no camera involved: standard PDC/WDF radii, the 20-segment clockwise order, and a `grid_geometry_mm()` generator (ring polygons, 20 sector boundary lines, label positions) that gets projected through any camera's homography for drawing.
- **`backend/calibration/auto_detect.py`** — best-effort seed, not a final calibration. **The 3 cameras are mounted around the board ~60° apart, looking at it from an angle, not face-on** — the double ring appears as an *ellipse* in-frame, not a circle. An earlier version of this module fit a plain circle, which put every landmark meaningfully off; it now fits an ellipse to the outer ring specifically (a narrow radial band, with an angular-spread sanity check, since a wide band or no spread check picks up red/green decorative logo text on the board's surround and badly skews the fit — found by rendering the fit and looking at it, not by trusting the numbers). Rotation uses the same Fourier-phase technique as before, now sampled around the fitted ellipse rather than a circle (sampling a circle across a genuinely elliptical board smears the wedge-alternation signal). **A landmark whose best-guess position would land outside the visible frame is reported as `null`**, not placed off-screen — this is what was silently breaking the manual UI (a "double 20" seed with a negative y-coordinate, invisible above the visible frame, for all 3 cameras). **Known limitation, by design:** this can find where wedge boundaries are but not which printed number is where (no OCR) — so a suggested landmark may land on the wrong wedge. That's what manual correction is for, and now `/auto` doesn't persist anything by itself — it only seeds the manual UI, which is the one thing that actually saves a calibration.
- **`backend/calibration/store.py`** — 5 reference points (image px) → `cv2.findHomography` → 3×3 matrix, persisted per camera in `config/calibration.json`. `grid_for(camera_id)` projects the board-space grid through the *inverse* homography into that camera's image space, so the frontend just draws returned coordinates — no homography math in JS. Landmarks are segments **20, 6, 3, 11** — a true 90°-apart cross in the standard clockwise segment order (double-6 sits opposite the board from double-11, both 90° from double-20/double-3). An earlier version used 20/5/3/11, which is *not* an even cross (double-5 sits right next to double-20, not opposite anything) — changed on request.
- **`backend/calibration/fine_tune.py` — colour-driven refinement ("Auto fine-tune"), built.** The 5-point manual solve is *exact* at those 5 clicks and free to drift everywhere else, which shows up as the grid sitting off the real wires on part of the board. This measures the board instead: a saturation-based mask picks out the double and treble beds (the only large red/green areas on the face), then each of the 20 segment centre lines is walked radially in board space to find where those bands actually are, and the homography is re-solved from ~40 spread correspondences with RANSAC instead of 5 clicks. It runs 3 passes, re-sampling through the improving fit each time.
  - *Saturation, not hue, is the discriminator* — measured on the real rig, the coloured beds sit at S≈140-165 while the cream/black beds are S≈45-55. Hue is still checked to reject the red decorative arcs and logo text on the surround, the same contamination that skewed the original auto-detect ellipse fit.
  - **Fit the band midpoint, not its edges.** Each bed is bounded by a wire that covers the true boundary, so the coloured run reads narrower than the real bed by about a wire width at each end. Those errors are equal and opposite about the midpoint, so midpoints are unbiased while edges are not — fitting to edges quietly tried to widen every band and biased the board's apparent scale. Switching to midpoints took camera 4 from 1.15mm→0.63mm mean instead of 1.31mm→0.93mm.
  - **Unlike `/auto`, this persists — but only on proven improvement.** It re-measures after fitting and refuses to save unless the mean error actually dropped, and rejects any fit that moves the board centre >80px or changes its apparent scale >25%. The endpoint returns before/after error in mm, sample and inlier counts, and the worst-remaining segments, all shown on the calibration card so pressing the button isn't a leap of faith. `store.save_homography()` re-derives the 5 stored points from the refined homography so `points` and `homography` can never disagree and the manual editor keeps working.
  - **Real-rig result:** on the three board cameras, mean ring error 1.27/1.29/1.15mm → 0.89/1.03/0.63mm, with 39-40 of 40 possible ring points found and ~85-100% RANSAC inliers. The signed per-segment errors before tuning showed the classic centre-offset signature — one arc reading ≈-2mm while the opposite arc read ≈+1mm — which is exactly the "grid is out on this run of doubles" symptom this was built for.
  - **Lens distortion was considered and measured, not assumed.** Fitting a radial k1 term alongside the homography on the same correspondences improved the residual by only 8-11% (k1≈-0.075 to -0.11), so it was left out rather than complicate the projection pipeline detection depends on for ~0.1mm. The remaining ~1mm residual is near the measurement floor set by the 0.4mm radial sampling step, wire width and board print tolerance.
  - *Method note worth keeping:* the first read of the before/after overlay images suggested a dramatic improvement — it was an illusion from viewing two stacked renders. Measuring the projected ring radius directly showed the centre had moved 0.7px and the radius 0.1%. **Render to look for problems, but confirm with numbers before believing a result.**
- **Manual calibration UI** (`frontend/src/components/ManualCalibration.vue`) — full-screen, zoomed to fill the viewport: click (or drag, if auto-detect/a previous save already seeded points) the middle plus the outer-ring edge of doubles 20, 6, 3, and 11, guided step-by-step; any landmark auto-detect couldn't place in-frame is simply left for the user to click, guaranteed visible since it's wherever they click. A fixed-corner magnifier (canvas `drawImage` cropping the live feed 4×, redrawn on every mouse move) gives pixel-precise placement without a following-cursor loupe obscuring the point being placed. Points and grid are all in the camera's *native* capture resolution — screen-to-natural-pixel conversion accounts for `object-fit: contain` letterboxing.
- Verified: a synthetic-homography unit check (known scale/offset reconstructs the double-ring radius to 5 decimal places), the full detect→suggest→grid pipeline against all 3 real board cameras' captured frames — including visually rendering the fitted ellipse and iterating on it after the first attempt was visibly wrong (drifted off the true ring, pulled by logo-text contamination) — and the full HTTP API contract (SPA routing, validation, save/clear, unknown-camera handling, off-frame points as `null`).
- **Found and fixed while testing against real hardware:** (1) the capture layer's hardcoded DirectShow backend couldn't reliably open the 3 external USB cameras at all (they're identically-named, on one shared hub — DirectShow's index-based enumeration doesn't consistently map to the same physical device); `capture/manager.py` now tries DirectShow first, falls back to Media Foundation, and confirms a real frame is readable before accepting either backend. (2) The dev server's `--reload` watched the entire project tree (`node_modules`, `.venv`) and appeared to hang after any backend edit; `backend/dev_server.py` scopes it to `backend/` only. (3) That fix's `reload_excludes` pattern, oddly, didn't pick up edits inside the new `calibration/` subpackage during live testing tonight (root-level files reloaded fine; nested-package files didn't) — worked around by restarting rather than root-caused; worth revisiting if it recurs.

### Detection pipeline (per throw) — rebuilt around multi-camera axis fusion

**This is the second detection architecture, not an incremental fix of the first.** A single-camera "find the tip point in each image" design (motion gate → isolate → tip-heuristic → per-camera score, no cross-camera check) got a real first-throw test back "not good, very slow." Root-caused directly against real footage (see below) and fixed at the noise level, but a second real-throw test with 3 darts still came back wrong on 2 of 3 cameras despite that fix — the *architecture* was the problem, not just tuning. The user supplied a reference implementation (`DART_VISION_HANDOFF.md` + working source, ported from `alternative-project/dartlab/`) built around a fundamentally different, better-founded technique, described below. The old `motion.py`/`isolate.py` single-tip modules have been deleted, not kept alongside.

**Why single-camera tip-finding doesn't work:** deciding which end of an elongated 2D silhouette is "the tip," from one oblique camera view, is genuinely ambiguous — no heuristic (width profile, PCA, whatever) reliably resolves it, because the information to resolve it isn't fully present in one 2D view of a 3D object.

**The fix — fit a line, not a point, then intersect across cameras:** each camera fits the dart's whole *axis* as a line (a much better-posed problem — RANSAC + Huber refit over the changed-pixel cloud, not "which end"). The tip sits at board level (z=0); the rest of the dart rises above it. Projecting a camera's 2D axis through its own image→board homography (which is only strictly valid at z=0) gives a board-plane line that still passes exactly through the true landing point, but is skewed elsewhere by that camera's own oblique angle in a way that depends on the camera. One camera's line alone can't say *where* on it the tip is — but two cameras' lines, each independently guaranteed to pass through the true point, pin it down exactly at their intersection. No per-camera tip guess, ever.

1. **Axis detection** (`backend/detection/axis.py`, ported) — per camera: restrict to a board-shaped ROI (physical board radius + margin, not just the scoring radius — a dart can land in the "OUT" zone and that's a real, scoreless throw, not a detection failure); phase-correlate pre/post frames to cancel tiny camera jitter before diffing; normalize each frame by its own ROI median/IQR (lighting-independent); **adaptive (MAD-based) threshold**, not a fixed one — `max(11, median + 5.5×noise_sigma)`, self-tuning to whatever noise level *that* camera has right now rather than a hand-tuned constant; RANSAC line fit (320 iterations, Huber refit) over the resulting changed-pixel cloud; reject if too short (<24px) or not elongated enough (<2.0 aspect) to be dart-shaped. Returns an `AxisCandidate` with a confidence blending support-pixel count, length, inlier ratio, elongation, and noise level.
2. **Fusion** (`backend/detection/fusion.py`, ported) — intersects every pair of candidate axis lines on the board plane; a 3-camera hit does a confidence-weighted least-squares solve across all 3, checks residual (how well the lines actually agree) and spread (how consistent the pairwise crossings are) against strict/review/reject thresholds; drops a clear single-camera outlier from a 3-camera set; has a deliberately narrow, evidence-gated path to recover a 2-of-3 result when exactly one pair uniquely crosses the physical board and the third camera is unambiguously wrong (always `review_required`, never auto-accepted); adds a small confidence bonus only when positional uncertainty sits safely inside one scoring region, never when it's ambiguous near a wire boundary.
3. **Session state machine** (`backend/detection/session.py`, ported with one deliberate simplification below) — one `DetectionSession` watches every configured+calibrated camera *together*, not independent per-camera workers: `learning_baseline` (median of 5-7 buffered frames, far more noise-resistant than a single frame) → `ready` (adaptive-threshold change-ratio watching, with a slow background-adaptation for genuinely static drift) → `settling` (freeze pre-frames, wait a minimum settle time, then collect consecutive *stable* post-frames — a wobbling dart doesn't get analyzed mid-wobble) → `_analyse_event` (axis-detect each camera in parallel intent, fuse, broadcast) → `cooldown` (avoid immediately mis-learning the just-landed dart into a "no dart" baseline, but still catch a genuinely quick next throw). Camera snapshots are synchronized with a small grace window so one camera being a frame behind doesn't get silently dropped from most events.
4. **Board-plane scoring** (`calibration/board_model.py` + `detection/scoring.py`, extended) — added the OUT-ring distinction (physical board face ~225.5mm vs scoring radius 170mm — landed on the board but off the scoring area is a real zero-value throw) and `distance_to_scoring_wire` (shortest distance to any score-changing boundary, circular or radial-segment, needed by fusion's uncertainty/confidence math). Kept this project's own sign convention (already validated) rather than the reference's; the geometry itself is convention-agnostic once expressed consistently.

**Verification, each layer checked before moving to the next, same discipline as the first architecture:** `score_board_point`/`distance_to_scoring_wire` re-verified against the original 9/9 synthetic-camera test plus known wire-boundary points; `detect_dart_axis` re-tested against the *exact two synthetic cases that were wrong under the old tip-heuristic* — both now recover a line passing within 1-5px of both true endpoints (previously up to 35-49px wrong) — and against real live idle-camera frame pairs, 0/5 false positives with the changed ratio landing at exactly 0.000% (cleaner than the first architecture's hand-tuned fix); `fuse_axes` tested with mathematically exact synthetic lines through a known point (triple-20, recovered to floating-point precision, correctly labeled T20, accepted) plus outlier-rejection, parallel-axis-rejection, and 2-camera and 1-camera-insufficient edge cases; the full `DetectionSession` state machine tested against real idle cameras end-to-end (start → `learning_baseline` → `ready` in 4.5s, stayed stable, clean shutdown) and, separately, against a synthetic 3-camera dart landing driven through the real trigger→settle→analyse→fuse chain using all 3 cameras' real saved calibrations (correct segment recovered, ~10mm position error consistent with the test's own synthetic-line imprecision, not a fusion bug — the tighter mathematical-line test above already showed the fusion math itself is exact); full HTTP API contract re-tested (start/stop/status, SPA routing). The thread→asyncio→WebSocket broadcast bridge proven working for the first architecture carries over unchanged.

**"After a few darts the board says ready but stops detecting" — two self-inflicted regressions.** Both were introduced by earlier fixes in this same session, and both were invisible because the detector reported a perfectly healthy `ready` state throughout.

1. *A stale occupancy reference silently ate every throw.* The takeout check (below) compares board occupancy against a session-start reference. Nothing bounded that reference's validity, so once it drifted — a nudged camera, a lighting change — occupancy sat permanently high (**12.6% observed live**, against ~1% for a real dart and 0.2% for an empty board). Every later event was then compared to that inflated figure, and ordinary downward noise read as "darts were removed", so genuine throws were discarded as takeouts and never scored. Fixed with a plausibility ceiling (`MAX_PLAUSIBLE_OCCUPANCY = 6%`, comfortably above the ~3% of three real darts): beyond it the reference is treated as stale, re-anchored, and the throw scored normally, rather than trusted forever. A simulated nudged camera reads 25.2% and is now caught instead of swallowing darts.
2. *A full baseline relearn after every single dart.* The LED-flash suppression fix ended by calling `_reset_to_baseline()`, which forced a complete `learning_baseline` cycle — 5 buffered frames from every camera, and (per the earlier all-cameras fix) a wait of up to **8 seconds** if any camera lagged. That relearn was never necessary: `_analyse_event` already stores the post-impact frames as the baseline, and those are captured *before* the flash under the same white light the strip reverts to, so they are already correct with the new dart included. Removing it cuts the blind window after each dart from **1.0–8.9s to ~0.85s**, which is just the flash suppression itself. Relearns that do still happen (after a takeout) no longer wait on the full first-run timeout either — once a session knows which cameras work, it waits only for those, and only 0.6s.

*Lesson worth keeping:* both bugs degraded silently while `/status` said `ready`. Any state the detector derives and then trusts indefinitely — a reference frame, a camera list, a baseline — needs either a validity bound or a visible health signal, or it fails quietly in a way that looks like the hardware's fault.

**Refinement against the user's own corrections — the first time tuning had ground truth.** Once real throws had been corrected by hand, the corrections themselves became a labelled benchmark: replay each corrected event through the real axis+fusion pipeline from its stored evidence frames and score against the position the user marked. Two things fell out, one of them an earlier fix of mine that was doing net harm.

*Benchmark hygiene first.* The replay assumes the previous event's frame is the "before" image, which is only true if nothing happened in between. One event showed 8-18% board change between frames (a hand had reached in), and it alone was inflating mean per-camera line error from 3.85mm to 23.59mm. Pairs are now validated (a single dart changes ~0.5-2% of the board) and invalid ones excluded — without that check every conclusion below would have been drawn from noise.

1. **The support-gap rejection was actively harmful and is now disabled.** It had been calibrated on a *single* failing throw. Measured against the corrected set it showed no relationship to accuracy at all — a 79% gap produced a 2.20mm line (the best camera of that throw) while a 15% gap produced a 7.74mm one — and it was discarding the single most accurate camera on two of three throws, forcing a two-camera solve that amplified the error. The bridging failure it was written for is already fixed at source by scoring `_contiguous_extent`, so the gate was pure loss. Removing it: mean position error **7.78mm → 5.27mm**.
2. **Least squares let one bad axis drag the answer; the fusion solve is now robust.** The best camera *pair* was consistently better than the three-line fit (4.22mm vs 7.35mm on one throw). `_robust_refine` adds Tukey-biweight IRLS: all three cameras stay in play, but axes that miss the emerging consensus lose influence. Result **2/3 → 3/3 correct**, mean error **5.27mm → 5.00mm**, with one throw flipping from a wrong segment to the right one. The tuning constant sits on a broad plateau (3-8mm all behave identically), so it is not knife-edged; it is applied only with 3+ cameras, leaving the two-camera path untouched. **This second finding was later shown to be luck and has been reversed — see "A 25 scored as S3" below. The `_robust_refine` code and its reasoning are kept; it is simply held back until there are four cameras, where it becomes valid.**

*Honest limits:* this is three usable events. It is enough to justify deleting a heuristic that demonstrably fires the wrong way, and to prefer a robust estimator over a non-robust one on general principle, but it is not enough to claim a validated accuracy figure. The benchmark harness matters more than the numbers — it is the beginning of the offline regression loop PLAN.md always wanted, and every future correction enlarges it. Residual ~5mm error still costs segments within ~20mm of the bull, where a bed is only 5-8mm wide.

**RANSAC bridging disconnected blobs — a confidently-wrong axis (found on a real "T6" that was actually S10):** a throw scored T6 with 26.7mm position error. Reconstructing each camera's line from the stored pairwise intersections showed cameras 2 and 4 were accurate to **2.3mm** while camera 3 was **53.8mm** off — and the camera-2/4 pair intersection alone was within 3.6mm of the truth, so the information for a correct answer was present and the least-squares fit over all three lines threw it away. Re-running `detect_dart_axis` on the saved evidence frames reproduced it exactly: in camera 3 only the dart's *flight* registered as changed pixels (the shaft/barrel had too little contrast against its bed), and RANSAC drew a line from that flight to an unrelated **24-pixel speck of noise ~450px away**, reporting `elongation=27` and `confidence=0.942` for a line 55mm wrong. Root cause was structural, not tuning: RANSAC's own score was `inlier_count × extent`, which *rewards* bridging two distant blobs — a flight plus a far-off speck scores higher than the real dart. Fixed in `axis.py` by (a) scoring `_contiguous_extent` (longest run with no gap > 25px) instead of raw extent, removing the incentive, and (b) rejecting any fit whose support is split by a gap > 35% of its span (`_support_gap_ratio`) — a dart is one continuous object. The statistic separates cleanly on real frames: **0.047 / 0.053 for the two accurate cameras vs 0.841 for the broken one**, a 16× margin, so the threshold isn't a guess. The event now fuses to the correct **S10**, accepted, 4.9mm error, from cameras 2+4. Verified no over-rejection against every real frame pair on hand plus synthetic clean-dart and split-blob cases, and the exact-line fusion test still recovers T20 to 0.000000mm.

*Design principle this settled:* a wrong-but-confident axis is far worse than no axis — with three cameras, dropping one still leaves a solvable 2-camera intersection, so the detector should refuse rather than guess. Rejected cameras now report *why* via `camera_notes` on the hit payload (shown in the UI), since a camera silently vanishing from `cameras_used` previously made 2-camera results indistinguishable from "the third camera saw nothing".

**A 25 scored as S3 — a robust estimator with nothing to be robust about, and the first real regression benchmark.** A dart in the outer bull was reported `S3`. It was not a wild miss: the fused point sat **16.97mm** from the board centre against a bull wire at **15.90mm** — over the line by **1.07mm**, and the detector already knew, marking it `review_required`, `score_uncertain`, wire distance 1.07mm against its own 5.58mm uncertainty. Not calibration either: the projected board centre was checked against the real inner bull in each camera's own frame (0.27 / 1.03 / 1.56mm).

- **What actually decided the score.** Camera 2's board line never came closer than **16.85mm** to the centre, so it was geometrically incapable of a 25; cameras 1 and 3 crossed at 76° *inside* the ring. In camera 2's own image the fitted axis missed the true landing point by only **10.4px** — about a barrel width — but that view is foreshortened enough at the bull that 10px is 6.4mm on the board, and the whole outer bull is only ~22px across. The fused answer was **exactly** the camera-2 × camera-3 crossing: `_robust_refine` had driven camera 1's weight to zero for sitting 6.34mm from that crossing, just over `ROBUST_TUNING_MM`.
- **Why the estimator could not have been right, and it is not a tuning matter.** Fitting a point (2 unknowns) to N lines leaves N−2 spare observations; three cameras leave exactly one, which is not enough to identify an outlier. Demonstrated rather than asserted: take three lines through a common point, displace exactly one, and the pattern of "how far is each line from where the other two cross" comes out **identical up to scale whichever one you displace** — for every camera layout, evenly spread or not. So the residuals the biweight iterates on carry no information about which line moved, and what it converges on is a *pair*, chosen by whichever cameras carry more confidence — and axis confidence measures pixel count, length and elongation, none of which say whether a line points in the right place. Camera 2 scored the maximum 0.98 while being the wrong one. `_robust_refine` is now gated behind `ROBUST_MIN_LINES = 4`, kept rather than deleted because the argument genuinely stops applying at four cameras; a broken camera is still handled by the evidence-gated `OUTLIER_PAIR_*` recovery path, which was always the right mechanism for it.
- **A latent bug the self-check found on the way.** When the biweight zeroes all but one line, `lstsq` does not fail — it returns the minimum-norm solution *along* that line, which lands wherever it passes closest to the origin. On a synthetic four-line case that put a treble 20 **79mm** away. It now checks the active set's conditioning and keeps the previous estimate.
- **`tools/detection_bench.py` — the replay benchmark, and the reason any of this is believable.** Every stored event is re-run through the *real* axis and fusion code from its saved evidence frames, with the previous event's frames as the "before" picture, the pre/post validity check from the section above, and a stricter one: a replay only counts if it reproduces the result the live detector actually produced, otherwise the chosen "before" frame was wrong and the case says nothing. Clip library in `clips/bench/` (frames + `status.json` + hand-written `labels.json`); `--capture` pulls a fresh one off a running server. Holding the reweighting back took segment/ring accuracy from **4/7 to 6/7** with no case getting worse — the 25 and a bullseye both stopped being called wrong. `--selfcheck` runs the identifiability demonstration and the fusion assertions as executable checks, since the project has no test framework.
- **A tempting change that was measured and rejected.** The all-pixels line fit lets a wide flight outvote a thin barrel, so a width-unbiased centreline fit (one median point per cross-section) looks like the obvious improvement, and it *did* show 7/7. But sweeping the one knob it has — 6 to 56 cross-sections — flipped the extra event's answer back and forth (7/7, 6/7, 7/7, 7/7, 6/7, 7/7…) while its underlying position error stayed at ~4.3-4.9mm throughout. It was moving a 4.5mm error across a triple wire by luck, not reducing it. Not shipped. *This is the same trap as the support-gap rejection above, caught earlier this time only because the benchmark existed to catch it.*
- *Honest limits, again:* 7 labelled events, one of them with a hand-placed position. The fusion change is carried by the proof rather than by the seven events, which is the only reason it is worth making on this much data. **Still not fixed:** ev4, an S10 that was really a T10, sits 4.7mm out and is unaffected — the per-camera axis error itself is untouched, and that is where the next real accuracy is.

**Every removal fired *two* takeouts, so a player was skipped every turn.** Reported as "sometimes it has it right and then sometimes messes up a game". The takeout log from a real game showed it exactly, three times out of three, in matched pairs:

```text
#2 awaiting=T occ=2.915%  "board changed while waiting for the darts to come out"
#3 awaiting=F occ=0.000%  "board occupancy fell 2.92% to 0.03%"
#4 awaiting=T occ=1.988%  ...  #5 awaiting=F  "fell 1.99% to 0.02%"
#6 awaiting=T occ=1.491%  ...  #7 awaiting=F  "fell 1.49% to 0.00%"
```

The first of each pair is the real takeout. The second is a phantom: `_occupancy` was only ever updated inside `_analyse_event`, and three of the four takeout paths return before reaching it, so it kept describing the board as it was *before* the removal. Clearing then relearned the baseline on the now-empty board, and the first event analysed afterwards read 0.03% against a stored 2.92% — a huge drop — and fired again. Each takeout calls `next_turn()`, so the turn advanced twice and a player never got to throw. Fixed by re-measuring occupancy in `_learn_baseline`, the one place that always runs when the detector commits to a new baseline; `tools/takeout_trace.py selfcheck` reproduces the whole sequence with synthetic frames and fails without the fix. *This is also why the "DARTS DETECTED AS REMOVED" banner appeared stuck: `_takeout_checkpoint` only clears when a dart is next scored, and the skipped player never threw one.*

**The turn now advances when the board is confirmed EMPTY, not when a hand appears.** Fixing the stale occupancy above closed one of the two paths to a double takeout; a log captured accidentally a day later showed the other still firing — `fell 1.03% to 0.05%`, i.e. *one dart* left in the board, pulled later and read as a fresh removal. Root cause: `clearing` ended on stillness alone, and stillness is not the question. A hand held over the board for `CLEARING_STABLE_SECONDS` is perfectly still, and so is a board with two of three darts still in it — so the baseline got relearned with darts (or a hand) in shot, and whatever came out next looked like a new event.

Stillness is now only the *gate on measuring*: a hand in shot makes occupancy meaningless, so wait for it to leave, then ask what actually matters — is the board clear? The separation is comfortable rather than marginal, which is why this is a reliable test: on real frames a single dart measures **1.0–1.5%** of the board and an empty one **0.00–0.05%**, against an `EMPTY_BOARD_OCCUPANCY` of 0.2%. `_handle_takeout` no longer advances the turn at all; that moved to `_finish_clearing`.

Two cases stopped this being a one-line change:

- **Mid-turn takeouts must not hold.** A hand reaching past the board while a player still has darts fires the same triggers, and waiting for an empty board there would block the rest of their turn — worse than the bug being fixed. So the hold only applies when the turn was full when the darts started moving; otherwise stillness alone resumes play. The turn still advances only if the board genuinely emptied, so a hand reaching past now costs nobody a turn, which the old code got wrong in the other direction.
- **The human can get there first.** Pressing "Darts removed" while the detector is still waiting clears `awaiting_takeout`; advancing again on confirmation would skip a player. `_finish_clearing` detects that and stays out of the way.

There is deliberately **no timeout that gives up and advances anyway** — a turn must never move on with darts in the board, and the button is always there for a dart that is genuinely stuck. The only escape is `MAX_PLAUSIBLE_OCCUPANCY`, for when the reference itself has gone bad and "empty" is unreachable. While holding, the detector says so (`Darts still in the board (1.5% covered)`) rather than stalling silently. Six new assertions in `tools/takeout_trace.py selfcheck` cover all of it, and were checked against the old stillness-only implementation to confirm they fail on it.

**Crowning a Killer looked like a takeout, because LED suppression lived at the wrong level.** The detector blinded itself around its *own* two flashes (`throw.detected`, `takeout`) by setting `_suppress_until` at those two call sites. Every cue the game engine fires went `_flash()` → `led_controller.flash_cue()` with detection wide open — and Killer crowns a player with the `bullseye` cue, so the relight registered as a board-wide change, i.e. as the darts being removed. Cue durations reach 5.0s (`game.win`) against a 0.85s `FLASH_SUPPRESS_SECONDS`, so even the two guarded paths would have under-covered a long cue. Suppression moved into `LedController`, which every cue already funnels through: `send()` marks the board busy for a settle margin, `flash_cue()` extends it across the cue's full duration, and detection reads `lighting_busy_until`. One check now covers every caller, present and future, with no per-caller wiring — the same shape of bug as the four drifted takeout triggers.

**Recovering from a takeout that got it wrong — `previous_turn`, and a banner that stops shouting when it was right.** The "DARTS DETECTED AS REMOVED" overlay stayed up until the next dart was *scored* (`_takeout_checkpoint` only clears in `_apply_dart_locked`), so a correct detection left a full-screen banner sitting over the board announcing something that had gone right — and during the double-takeout above, over a player who never threw. Split by phase instead: darts still in the board keeps the centre-screen instruction, but once detection has acted it collapses to a corner tab naming who is up, which opens the corrections only if it was wrong. `MatchEngine.previous_turn` is the recovery — the same replay-the-log mechanism as `undo_dart`, rewinding to *before* the last turn change rather than stepping the player pointer back, which is the only version that hands the previous player their own darts back instead of a fresh turn, and that discards whatever landed in a turn that should not have started. The corrections (dart count with remove/add-a-miss, previous player) are one `TurnCorrections` component shared by the big screen and the phone. The confirm button also had to stop being one button doing two jobs: it always called `confirm_takeout`, which is right while the darts are in the board but a *no-op* once detection has already advanced the turn — it rewinds to the prompt and re-advances, landing exactly where it started, so the button looked broken (verified: player unchanged, versus `next_turn` correctly moving on). It now picks the endpoint by phase, and only says "Darts removed" while there are actually darts to remove. Both takeout overlay and action grid had been duplicated *byte-for-byte* three times in `PlayView.vue`, once per game layout, which is why they are now components — five parallel copies of a control set is how the four takeout triggers drifted apart in the first place.

**First real-throw test, 2 of 3 wrong (adjacent segment each time), root cause, fix:** first live test with 3 real darts came back "S18 correct, T17 read as S2, S14 read as S11" — both misses landed on the segment *immediately adjacent* on the board, the classic signature of a rotational calibration offset. Checked the calibration source for all 3 cameras (all "manual," not rough auto-detect) and rendered the live camera frames with the current stored calibration grid (rings + sector lines + segment labels) overlaid — visually well-aligned near both boundaries on all 3 cameras, not a gross miscalibration, and S18 being correct rules out a uniform rotational offset across all cameras (that would misclassify far more than 2 throws). Traced instead to a real gap in `fusion.py`: it already computes `score_uncertain` (true when the fused point's distance to the nearest scoring wire is within the position's own measurement uncertainty — exactly "this could genuinely be either segment") but the accept path (`accepted = geometry_within_limits and (normal_confidence or strong_three_camera_consensus)`) never checks it — a hit can be fully `accepted` with high geometric confidence while still being `score_uncertain`, i.e. confidently reporting a segment that's a coin-flip against its neighbour. The UI never surfaced `score_uncertain` for accepted hits, so a genuinely-uncertain-but-accepted call looked identical to a dead-center one. Fixed on the UI side (not by changing the accept gate itself, which is about geometric quality, a separate and correct concern): the hit panel and event log now show a distinct "near a wire — verify" warning, with the actual wire distance vs. positional uncertainty in mm, whenever `accepted && score_uncertain`. Also added a bounded 40-entry `DetectionSession._history` (exposed via `/api/detection/status` as `history`) — previously only the single *latest* event was queryable, which meant a specific past throw's numbers (confidence, wire distance, uncertainty) couldn't be checked after the fact at all; this was the actual blocker in diagnosing which of the two misses were even `score_uncertain` cases, not something confirmable directly. *Still open: whether every "near a wire" real miss is now correctly flagged needs another live-throw test aimed at wedge boundaries specifically, since the two original misses themselves couldn't be retroactively inspected.*

**Deliberate simplification vs. the reference implementation:** takeout/"board obstructed" handling here is a single large-area-change trigger that relearns the baseline, not the reference's separate empty-board-reference-matching and scene-clear-wait states tied to a game engine's `pending_review`/`awaiting_takeout` signals — this project doesn't have a game engine yet (Phase 3) for those richer states to report into. Worth porting once it does. Camera *identity* (the reference's persistent-ID-based left/top/right role mapping, robust to a Windows index reshuffle after reboot) was also not ported — this project still keys cameras by device_id from Setup, which is a real latent fragility already noted in Phase 0's capture-layer findings, worth revisiting together.

**Still not real-throw-validated end to end** — the axis/fusion approach is verified thoroughly against synthetic ground truth and real camera noise, but hasn't yet been watched catching an actual sequence of real thrown darts. That's the next step.

**Delivery:** `backend/detection/pipeline.py` runs one `DetectionWorker` thread per actively-watched camera (via `POST /api/detection/{id}/start`/`stop`), sharing the same ref-counted `CaptureManager` as live preview/calibration. Broadcasts `detection.dart` / `detection.takeout` over the existing WebSocket `Hub` - verified with a real background thread calling `asyncio.run_coroutine_threadsafe` into the server's actual running event loop, confirming the exact payload reaches a connected client (this thread→asyncio bridge was the highest-risk new plumbing here). A debug UI (`frontend/src/views/DetectionView.vue`, `/detection`) shows the live feed with a marker at each detected tip and its score, a start/stop button, and an event log - built specifically so real throws can be watched and judged visually, since that's the only real validation available (no fabricated real-throw footage exists to test against).

**Still to do:** fusion across the 3 cameras (step 5 - currently each camera scores independently, no voting/agreement/confidence yet); validating and likely retuning `find_tip` against real thrown darts, which needs the dart lab (below) to do systematically rather than one throw at a time.

### The dart lab (this is what makes it tunable) — replay runner built

The offline harness, in the order it was originally planned:

- **Recorder** — *not built as a separate thing, and probably never needs to be.* `DetectionSession` already keeps the evidence frames every throw was scored from, so the recording is a by-product of playing. `tools/detection_bench.py --capture` pulls them off a running server into a clip library.
- **Ground truth tool** — *half built.* The in-app Override / miss dialog already produces hand-placed positions, which is the strongest label there is; `clips/bench/labels.json` is where they get written down, currently by hand. A "the detector got this wrong, here's the truth" export straight from the correction UI is the missing piece.
- **Replay runner — built** (`tools/detection_bench.py`). Runs the *real* axis + fusion code over the clip library and scores segment/ring correctness plus position error against the labels. Two hygiene rules do most of the work: the before/after pair has to show a plausible single-dart change (a hand reaching in between two events once inflated mean line error from 3.85mm to 23.59mm on its own), and the replay has to reproduce the result the live detector actually produced, or the "before" frame was wrong and the case is excluded rather than counted. `--selfcheck` runs the fusion geometry assertions, which is where the project's executable reasoning lives given there is no test framework.

This turns "fine tuning" from standing at the dartboard for hours into an offline, measurable loop — and it doubles as your test suite on both Windows and Pi. It has already earned itself twice: it carried the `_robust_refine` reversal above, and it killed a plausible-looking centreline-fit "improvement" that was really just moving a 4.5mm error across a wire.

## Game engine — built (core + 8 games of a 20-game catalogue)

`backend/games/` — `base.py` (Dart/PlayerState/TurnResult/Game contract), `engine.py` (MatchEngine), `registry.py` (the catalogue), plus `x01.py`, `practice.py` (Round the Clock, Shanghai), `party.py` (Killer, Donkey Derby, Space Invaders) `golf.py` (Darts Golf) and `oxo.py` (Noughts & Crosses, from a full written spec - X vs O on a 3x3 grid of board targets, easy/standard/hard claim rules, win/block/centre/corner tactical hints, draw handling).

**`ADDING_A_GAME.md` is the how-to for adding the remaining 13.** Written to be handed to someone with no other context, and deliberately leading with the fact that a new game needs *no* frontend work: `PlayView.vue` only has bespoke layouts for Killer and Space Invaders, and everything else falls through to a general layout that already renders the score, the dart pips, the live board with `highlight_numbers()` lit on the actual beds, the player list and the full control set — while `GameArt.vue` falls back to a generic dartboard for any unknown `art` key. Golf was implemented from that document as a check that it is followable, which caught two wrong verification snippets in it: both fired darts into a turn the engine had already closed (`awaiting_takeout` rejects them), so the "undo is exact" test was comparing against the wrong dart and passed regardless. The corrected version asserts its probe dart was actually accepted, and was confirmed to fail against a deliberately non-deterministic game.

- **One shared core, as asked.** Darts arrive from the detection pipeline, players come from the existing roster, and `MatchEngine` owns turn rotation, dart counting, undo, the WebSocket feed and the LED cues. A game only decides what a dart *means* — it implements `apply_dart()` and `view()` and gets everything else free. Detection feeds the engine through a lazily-imported, exception-wrapped hook, so detection keeps working with no game running and a bug in a game can never take the detector down.
- **Undo is a replay, not state surgery.** The engine keeps an ordered action log and undo rebuilds the match from scratch without the removed dart. Games are deterministic, so this is exact and — unlike asking each game to implement an inverse of its own scoring — cannot drift out of sync as games are added. (Verified: undoing a T19 restored 324 → 381 correctly mid-match.)
- **Turn ends when the darts come out.** A finished turn sets `awaiting_takeout` rather than advancing immediately; the detection takeout signal (or the Next button) rotates the player. This is exactly the richer takeout state the detection section notes was deliberately left unported "until there's a game engine to report into" — there now is one.
- **Dart-removal mode is now stated on the board, not just implied.** The engine already refused to advance until the darts came out (`awaiting_takeout`), but the only sign of it was a small phase chip, so a finished turn looked like the app had missed a dart. A **PLEASE REMOVE DARTS** panel now sits over the board in all three layouts, names who is up next, and carries the Darts removed button. Relatedly, `submit_dart` used to *silently discard* a dart that arrived once the turn was full — a manually added dart, or one detected while someone reached in, simply vanished. It now sets a message saying the turn is over.
- **Detection keeps watching through the prompt, and the button outranks it** (`MatchEngine.confirm_takeout`). Showing the prompt doesn't pause auto-detection — most of the time it gets the takeout right and nobody touches anything. When it doesn't, it fails in two ways that both read as the app losing track: it fires early on a hand reaching in, or it fires twice and **skips a player entirely**. So the engine records a checkpoint (the action-log length) the moment the prompt appears, and Darts removed rewinds to it before advancing exactly one turn. If detection was right, rewind-and-advance lands on the identical state, which makes the button safe to press either way rather than something you have to judge.
  - The panel therefore stays up after detection acts, restyled from an orange "PLEASE REMOVE DARTS" to a calmer green "DARTS DETECTED AS REMOVED — not right? press below", so the override is always one press away. The phone's button carries the same authority via the same endpoint.
  - **The override window closes as soon as a dart is scored,** deliberately: nobody can throw while the previous player's darts are still in the board, so a scored dart proves the board really was cleared, and silently wiping a real turn would be a worse failure than the misfire being guarded against. Both halves of that trade-off are pinned by tests.
  - **A latent bug this depended on:** `_replay_locked` emptied `_log` and never repopulated it, so **undo only worked once** — the first undo threw the whole history away and every later one silently did nothing. Any rewind had no log to rewind to either. The replayed actions are now pushed back as they are applied.
- **Round the Clock re-checked against the published rules** ([darthelp](https://darthelp.com/games/how-to-play-around-the-clock-darts/), [dartcounter](https://dartcounter.net/games/around-the-clock)) after a report of targets swapping between players: advance within the same turn while darts remain, no skipping, progress kept between turns, any ring counts by default, finish on the bull. All confirmed correct by test, including a full engine run showing one player going 1→2→3 while the other stays on 1, and the hint switching to *that* player's target after the takeout. The reported swap could not be reproduced.
- **Shanghai advanced two rounds per cycle — found while checking the above.** It stored its target number in `self.round`, which the engine *also* owns and increments every time the turn order wraps back to the first player. Both fired at the end of a round, so round 1 jumped straight to 3 and half the board was never thrown at. Its counter is now `target_round`, and the engine's `round` is left alone. *Lesson: a base class attribute the engine writes to is not free for a game to reuse, however natural the name.*
- **Two finishing semantics, which is a real distinction and was a real bug.** `finish_player()` counts places up from 1 (X01, races: finishing first wins). `eliminate()` fills places from the bottom (Killer: the first player *out* comes last). Killer initially used the wrong one and crowned the first player knocked out as the winner — caught by the test suite.
- **Catalogue drives the UI.** All 20 games carry their own rules text, categories and three difficulty levels in `registry.py`, so the library screen, rules sheet and difficulty picker are generated — the frontend hard-codes no game knowledge. Games without an implementation are listed as "coming soon" rather than hidden, keeping intended scope visible.
- **Difficulty is real, not cosmetic:** Round the Clock switches between any-hit, doubles-jump-ahead and doubles-only; Killer's easy/medium/hard changes how many adjacent slices you own; Space Invaders' changes the fleet size. (X01 now takes its start score and finishing rule as explicit options instead — see the arcade rework below.)
- **Artwork is programmatic SVG** (`frontend/src/components/GameArt.vue`) — no image files to load, crisp at any size, works offline on the Pi. No AI image-generation tool is available in this environment (same constraint that made the avatars programmatic; see `tools/generate_avatars.py`). *Gotcha found by screenshotting rather than trusting the code:* a CSS `transform` in an animation overrides an element's SVG `transform` attribute, which collapsed the whole Space Invaders grid and two of three Donkey Derby racers onto the origin. Positioning now lives on an outer `<g>` and animation on an inner one.
- **Bright white is the playing state; colour is only ever a moment.** Every game cue is fired through `flash_cue` and returns to the full-brightness white resting state, and `start()`/`stop()` re-assert that resting state so whatever the LED page or a previous game left behind can't linger. This is not just aesthetics: the cameras are calibrated under flat white, and a slow colour wash across the board is precisely what the detector reads as motion. *Two bugs found here:* `game.start` and `turn.start` were fired with `cue()` rather than `flash_cue()`, so starting a game left a WAVE effect washing over the board for the entire match and every turn change left a green pulse — the worst possible lighting for detection. Separately, the 180 celebration was firing on any 60-point dart, i.e. every single treble 20; a bullseye now gets its own effect and everything else is left to the game.
- **Per-event effects,** each with a duration matched to the size of the moment: dart detected (green, 0.5s), turn change (comet, 0.7s), bullseye (1.5s), bust (police, 1.4s), 180 (celebration, 2.5s), game won (celebration, 5s). Verified by capturing every send: all seven fire the right effect at full brightness and land back on white on schedule, including three darts in quick succession, which must not strand a colour.
- **Starting a game starts the cameras.** These were originally separate — a game could be started with detection stopped, and the only symptom was that darts silently never appeared. `POST /api/games/start` now starts the detection session too and reports the outcome, but doesn't *fail* the game if it can't: a match is still perfectly playable on the manual keypad when the cameras aren't calibrated. The play screen also shows a live detector warning (not running / a camera sending no frames / still learning the board), because scoring quietly doing nothing is the worst failure mode this app has — and the same silent-degradation pattern already bit twice in the detection work.
- **Live themed board is the centre of the play screen** — the pattern taken from `alternative-project`'s `displayBoardSvg`, rebuilt on this project's own `dartboardGeometry`/`DartboardFace` rather than copied, so the bed geometry stays the single source of truth already cross-checked against `score_board_point`. Three things make it useful rather than decorative: the beds you need are **lit on the board itself** (`Game.highlight_numbers()`, so nobody has to translate a text hint while standing at the oche), each game picks a **palette** (`Game.theme()` — classic / killer / space / derby), and this turn's darts appear as **numbered pins at their real measured `x_mm`/`y_mm`**, falling back to the centre of the named bed for manually entered darts. Highlighting is genuinely dynamic: Killer lights your own double until you become a killer, then switches to every rival's; Space Invaders lights only the invaders still alive.
  *One convention worth noting:* the reference negates y when plotting (`[x, -y]`) because it works in a y-up board space. This project's `board_model` is y-down to match image space, which is also SVG's own sense, so no negation is needed here — copying that line verbatim would have mirrored every dart.
- **Manual entry on the play screen** enters a dart by hand — both the fallback when detection misses one, and how the games can be exercised at a desk with no board. Originally a 60-button keypad; now the **Override / miss** dialog's clickable virtual board (`BoardPicker.vue`), which covers every bed plus MISS in one tap and doubles as the correction UI.
- *Remaining:* the other 14 catalogued games; per-game LED choreography beyond the shared cues; match history/stats persistence.

## Arcade presentation rework — built (Killer, Space Invaders, X01)

Three games were rebuilt to match supplied design mockups: a pub-arcade look, a
per-game setup screen before the match, and a themed "arena" play screen. The
mockups are screenshots of `alternative-project`'s working UI, so its rules and
geometry are the reference — but as with `displayBoardSvg`, the presentation was
**rebuilt on this project's own components** (`DartboardFace`, the shared
`MatchEngine`) rather than copied wholesale.

- **Killer is a different game now, not a restyle.** It was "hit the double of your own number"; it is now *slice groups and marks*: each player is dealt a group of **physically adjacent** slices (hard 1 / medium 2 / easy 3), any ring counts, and three marks make you a Killer — doubles and trebles count as 2 and 3 marks. Killers then take rival lives, again multiplied by the ring. Because groups must not overlap on a 20-slice board, difficulty also sets the player cap (12 / 10 / 6), enforced with a message that explains *why* rather than a bare rejection.
- **Space Invaders is a lane shooter, not a wave counter.** A fleet of 20/31/45 aliens orbits the board in three rows; a numbered dart fires that lane, doubles/trebles fire 2-3 shots, shots damage front-to-back, back-row "tanks" take two hits. The outer bull arms a **Multi-Cannon** (the next numbered hit also fires the lanes either side); the inner bull fires one shot down all 20 lanes. Aliens advance after each completed round and three advances breach the grid, costing one of three shared defence lives. An optional round limit ends it on points.
- **Randomness had to be made replayable.** Both games deal randomly (target groups, which aliens advance), and the engine's undo *rebuilds the match from scratch* and replays the action log. A fresh `random` draw on each rebuild would have re-dealt everyone's targets on every undo. Both games now `options.setdefault("seed", ...)` on construction, and since the engine replays through `build_game(..., self.game.options)`, the seed travels with the game — verified by undoing a Killer mark and confirming the target assignment was untouched.
- **The gun actually shoots.** `SpaceStage.vue` watches `last_attack.id` and, on a new attack, fires lasers down the hit lanes, explodes the destroyed aliens and recoils the cannon barrel. The backend emits `last_attack` (type, lanes, damaged/destroyed ids, points) purely so the UI has something to animate — the alternative would be the frontend diffing fleet state, which can't distinguish "destroyed just now" from "already gone".
  *The `transform` gotcha from GameArt applies here too and was designed around from the start:* alien positioning lives on the outer `<g>` as an SVG attribute, every animation on an inner `<g>`, so a CSS `transform` can never collapse the fleet onto the origin.
- **Geometry checked by rendering it, not by reading it.** No browser automation exists here, but OpenCV does — so the playfield's lane angles, row radii, cannon placement and laser track were drawn to a PNG from the *real* backend formation and looked at. That caught a genuine clip: the outer row sits at radius 472 and alien bodies plus their points label reach past the `0 0 1000 1000` viewBox half-width of 500. The viewBox is now `-25 -25 1050 1050` — still centred on (500,500), so nothing moves, but nothing clips either.
- **Per-game setup screens** (`GamesView.vue`) replace the one-size difficulty sheet: Killer picks slice count, Space Invaders picks fleet strength plus an optional round limit, X01 picks a start score (201–701) and its finishing rule. X01's start score being explicit made the old three-difficulty ladder unreachable from the UI, which would have silently dropped straight-out play — so the finish rule is its own control, and both paths are tested (a single finishing a leg under straight-out, and the same single busting under double-out).
- **Fullscreen, and a phone can drive it.** The play screen has a fullscreen toggle, but the browser Fullscreen API can only be called on the machine that owns the screen — a phone cannot put the TV into fullscreen. So `backend/display.py` holds a `presentation` flag, broadcast over the existing `/ws` hub; the main screen reacts with a CSS fullscreen layout that hides the app chrome, and both the TV and the phone (`/join`) always offer the matching **restore**. Verified end-to-end, including a real WebSocket client receiving the broadcast.
- Verified: the rewritten rules directly (slice adjacency and disjointness at every difficulty and player count, marks→Killer→elimination, player-cap rejection, fleet sizes, front-to-back lane damage, Multi-Cannon arm-and-fire, bull barrage, breach→defeat, round limit, clear→victory) and the whole thing over real HTTP against the FastAPI app (catalogue shape, start with options, live `last_attack` payloads, undo determinism, X01 start score and both finishing rules, the display API and its WebSocket broadcast, SPA routing).

### Original design notes (still the intent)

- **Event-sourced:** a game is a fold over an ordered list of events (`DartScored`, `TurnEnded`, `DartCorrected`, `ThrowUndone`). Correction/undo = amend the event list and re-fold — no fragile state surgery. This is the single most important design choice for a scorer, because mis-detections *will* happen.
- **Game interface (plugin style):**
  ```python
  class Game(ABC):
      meta: GameMeta                     # name, description, min/max players, options schema
      def start(self, players, options) -> GameState
      def apply(self, state, event) -> GameState   # pure function
      def view(self, state) -> dict     # JSON for the UIs (scores, whose turn, checkout hints)
      def led_cue(self, state, event) -> LedEffect | None
  ```
- **Registry:** games self-register; the UI game-selection screen is generated from `meta`, including per-game options (501/301, double-out, etc.). Adding a game = adding one module.
- **Game library candidates:** x01 (first), Cricket, Around the Clock, Shanghai, Killer, Halve It, Bob's 27, plus practice/training modes (doubles practice, checkout trainer).
- Each game contributes its own Vue view component for themed graphics, registered by game id; shared components (player bar, dartboard SVG, turn indicator) come from the core UI kit.

## Front ends

- **Main GUI (TV/monitor):** setup, calibration wizard, game selection, live game screen with big themed graphics, correction overlay (tap the board SVG where the dart actually landed).
- **Phone GUI:** join via QR code shown on the main screen — **built**, no session token yet (see below). Player registration with selfie (native camera app via a file input, works over plain HTTP — see below) — **built**. Now a full four-tab remote (see Phone remote below) — **built**.
- **Shared:** one WebSocket protocol (`/ws`, built); UIs are dumb renderers of server state — the server is the single source of truth, so any number of screens can watch a game.

## Players engine — built

A shared roster is the natural thing to build before the game engine
proper (games consume a player list; this *is* that list), and it's also
where the app's live-sync channel between the main screen and phones had
to exist for the first time — so this doubled as the first slice of the
WebSocket "delivery" layer described above.

- **API** (`backend/players/routes.py`): `GET/POST /api/players`,
  `PATCH/DELETE /api/players/{id}`, `POST/DELETE /api/players/{id}/selfie`.
  Every mutating call broadcasts the full roster over `Hub`
  (`backend/events.py`) as `{"type": "players.updated", "players": [...]}`.
- **Rules** (`backend/players/store.py`): defaults to 2 players; add/remove
  clamped to 1–8 (solo play is explicitly allowed — minimum is 1, not 2);
  names deduplicate default "Player N" labels; avatar defaults cycle through
  an unused gallery entry so new players look distinct without any action.
- **Persistence:** `config/players.json` + `config/selfies/{id}.jpg`, so the
  roster survives a restart — same pattern as `settings_store.py`.
- **Frontend:** one shared `PlayerRoster.vue` component (rename inline,
  add/remove, `AvatarPicker.vue` gallery-or-selfie) is used by both
  `/players` (desktop, top nav shown) and `/join` (phone, no nav) — no
  duplicated UI logic between the two interfaces. A Pinia store
  (`stores/players.js`) opens the `/ws` connection and keeps `players`
  reactive; a change on a phone reaches the main screen in one WebSocket
  round-trip, no polling.
- **Avatars — important caveat:** "AI generated" avatars aren't actually
  wired up — there's no image-generation tool available in this build
  environment. `tools/generate_avatars.py` instead produces 12 deterministic
  programmatic placeholder mascots (SVG, varied color/feature combinations)
  into `frontend/public/avatars/`, giving the same "pick from a gallery,
  defaults if you don't" UX. Swap in real generated art later by dropping
  same-named files into that folder, or point the script at an actual image
  API and keep the output paths the same.
- Verified: full CRUD + min/max enforcement + selfie upload/fetch/clear via
  an in-process test client, and — separately — two concurrent live
  WebSocket connections over the real network stack both receiving an
  identical broadcast after one of them changed the roster (the actual
  main-screen + phone scenario this was built for).
- **Selfies work over plain HTTP — resolved without HTTPS.** Found on real
  hardware: browsers only expose `getUserMedia` (the live-camera-stream
  API) in a secure context (HTTPS or `localhost`); a phone on plain
  `http://<lan-ip>` gets `navigator.mediaDevices === undefined`, which
  first surfaced as a raw crash. Tried a self-signed-cert fix
  (`tools/generate_dev_cert.py` + uvicorn `--ssl-keyfile`/`--ssl-certfile`),
  then backed it out on explicit feedback — every device shows a
  "connection not private" warning on first visit, bad UX for a party app.
  Fixed properly instead by dropping
  `getUserMedia` entirely: `SelfieCapture.vue` now uses
  `<input type="file" accept="image/*" capture="user">`, which hands off to
  the phone's native camera app via the OS file picker — a different code
  path, not gated behind a secure context, works over plain HTTP on iOS and
  Android alike. Trade-off: no live in-page preview before the shot (native
  camera app's own viewfinder instead), a fair swap for not needing HTTPS.
  The self-signed-cert script remains in the repo, unused, in case some
  other future feature genuinely needs a secure context.

## Phone remote — built

`/join` is now a four-tab phone app (**Play / Games / Players / Lights**) rather than just the player roster. One WebSocket and one poll live in `JoinView.vue` and the game state is passed down, so switching tabs doesn't open a connection per tab.

- **Starting a game takes over the main screen, wherever it was started from.** Two separate faults: the big screen only routed itself to `/play` when *it* had started the game, so a phone start left it sitting on the games library; and nothing put it into fullscreen. Now `POST /api/games/start` flips presentation mode on (and `/stop` flips it off), and a small WebSocket listener in `App.vue` routes any non-phone screen to `/play` as soon as a `game.state` broadcast says a game is active. The listener only routes — the play view keeps its own socket and all rendering, rather than creating a second source of truth. The desktop's own Start button additionally requests the *real* Fullscreen API, issued before any `await` so the click still counts as user activation; a phone can never do that, which is exactly why the server-side presentation flag exists.
  - *Bug this surfaced:* arriving at `/play` already fullscreen fired no `fullscreenchange` event, so the play view thought it wasn't fullscreen and never applied the layout. The body classes are now derived with `watchEffect` from state instead of being set by each event handler, so every way in — navigation, a remote toggle, a game starting — lands in the right layout.
- **Play** (`PhoneGameControls.vue`) — whose turn, this turn's darts, the live scoreboard, and the same four actions the main screen has, sized for a thumb: Override / miss, Record complete miss, Undo dart, Next player (which relabels to "Darts removed" when the engine is awaiting takeout). "Replace last dart" is an undo followed by the corrected dart, which is exact because the engine rebuilds a match by replaying its action log rather than doing state surgery.
- **Games** (`PhoneGameLibrary.vue`) — the full catalogue with the same per-game options as the desktop setup screens (Killer slice count, Space Invaders fleet + round limit, X01 start score + finish rule), so a game started from a phone is identical to one started at the TV.
- **Lights** (`PhoneLeds.vue`) — deliberately effect-first, not settings-first: quick-pick effects, colour, brightness, speed, and one-tap previews of every named game cue. Transport wiring stays on the desktop LEDs page, since that's a one-time setup job. Verified to degrade gracefully with no controller attached.
- **The zoomable board is the point of the whole thing** (`PhoneBoardPicker.vue`). A mis-detected dart is almost always *one bed away* from the truth, so the two candidates are adjacent and small — at full-board zoom a treble bed is a few millimetres of screen and a fingertip covers several. It supports pinch-zoom (to 6×), drag-pan, wheel zoom, and a marker showing exactly where the finger landed, with the choice confirmed in text ("Treble 20 · 60 points") before it's applied. `touch-action: none` stops the page scrolling underneath. Text and stroke widths are sized as a fraction of the current view, since everything inside the SVG is in board millimetres and would otherwise balloon when zoomed.
  - **Which bed a tap selects is computed from geometry, not SVG hit-testing**, so a tap that lands on a wire still resolves. Cross-checked against the backend's independent `score_board_point` on **4240 points — zero mismatches**, including points placed ±0.6mm either side of every ring boundary on all 20 segments.
  - **Two gesture bugs found by replaying touch sequences, not by tapping.** Lifting the second finger of a pinch looks exactly like a one-finger tap (one pointer left, no movement since the last move event) and silently re-selected whatever bed sat under it — now suppressed by a `pinched` flag that latches for the whole gesture. And panning died after a pinch until both fingers were lifted, because pan state was only seeded on first touch — the remaining finger now takes over panning. Both are covered by a gesture simulation that replays down/move/up sequences against the state machine.

## LED / ESP32 integration — built

The ESP32 firmware (`led-controller/`, PlatformIO/Arduino) drives a WS2812B
strip via FastLED and accepts an identical JSON state schema
(`on`/`bri`/`fx`/`sx`/`col`/`col2`) over **two transports at once**:

- **USB serial** — one JSON object per line at 115200 baud (`led-controller/src/serial_ctrl.cpp`). Works the instant the board boots; needed because a shared WiFi router isn't always available where the board is set up (e.g. venues, other people's networks).
- **WiFi HTTP** — REST (`GET/POST /api/state`, `/api/effects`, `/api/info`) at `http://led-controller.local`, plus OTA firmware updates. Connects in the background at boot; if it can't join, the board keeps running serial-only and retries periodically rather than blocking startup.

Both transports share one state parser (`state_json.cpp`) so they can never drift out of sync. 41 effects are defined in `include/effects.h`, split into generic animations (rainbow, comet, fire, …), solid colour presets, and game-feedback effects purpose-built for a scorer (`FLASH_3` for a registered dart, `POLICE` for a bust, `CELEBRATION` for a 180/win, `BULLSEYE`, `COUNTDOWN`, etc.).

**Backend hook — the single integration point for the rest of the app:**
`backend/leds/controller.py` exposes a module-level `led_controller` singleton. Calibration, the game engine, and API routes are all meant to call this rather than talk to the ESP32 directly:

```python
from leds.controller import led_controller
led_controller.cue("game.win")                    # named cue, tunable in settings
led_controller.send({"fx": "CELEBRATION", "sx": 15})  # raw state, fx by name or id
```

- **Fire-and-forget:** every send goes through a background worker thread with its own queue, so a missing/unplugged LED controller can never stall calibration or a game — verified by driving the full API with no hardware attached and confirming clean degradation (no exceptions, `connected: false`, errors surfaced only via `/api/leds/status`).
- **Transport selection** (`auto` / `serial` / `http` / `off`) lives in settings. `auto` prefers a detected USB serial port (matched by common ESP32 USB-UART chip VIDs) and falls back to HTTP — mirroring the firmware's own serial-first, WiFi-as-bonus philosophy.
- **Named cues** (`backend/settings_store.py` → `leds.cues`) map an event name (`calibration.start`, `throw.detected`, `bust`, `score.180`, `game.win`, …) to an LED state. New games/flows just fire a cue name; colour/effect tuning happens in settings without touching game code — the same "tune independently" principle as the rest of the architecture.
- **Web UI:** a "LEDs" page (`frontend/src/views/LedsView.vue`) configures the transport/port/URL, shows live connection status, and lets you preview any effect or named cue with one click — useful for tuning cues without writing game code yet.
- **Resting state + transient flashes** (`set_resting_cue` / `flash_cue`): no firmware effect self-terminates — the strip holds whatever state it was last given — so anything momentary only *looks* momentary because the backend sends the resting state back afterwards. `set_resting_cue("startup")` at app startup (`app.py` lifespan) both fires the cue and records it as what flashes return to; `flash_cue(name, duration_s)` fires a cue and schedules that revert. Repeat calls restart the countdown instead of stacking, so three darts thrown in quick succession hold the flash until a second after the *last* one rather than reverting mid-sequence; a lasting `cue(...)` cancels any pending revert so it can't be silently undone a moment later. Deliberately guarded by its own lock, not the transport lock — that one is held across blocking serial I/O (up to a 2s write timeout) and `flash_cue` is called from the detection thread, which must never stall on the LED link.
- **Current cue behaviour on real hardware:** `startup` = solid white at full brightness (doubles as task lighting for the cameras, not just decoration); `throw.detected` = solid green, fired from `DetectionSession._analyse_event` for 0.5s whenever fusion actually located a dart (`x_mm is not None`) — a "NO HIT" from disagreeing cameras deliberately doesn't flash. Verified end-to-end against the real ESP32 on COM9: white → green → back to white, plus unit-level checks of the rapid-repeat and cue-supersedes-flash cases.
- **The LED flash and the cameras fight each other** — found on the first real throw after wiring the flash up. The green flash relights the whole board, and the motion gate cannot distinguish a board-wide lighting change from a dart, so every real throw produced a phantom second "throw" (twice over: once going green, once reverting to white). Measured against real saved camera frames with a *conservative* green tint: the lighting change alone yields a 0.5–2.5% change ratio against a 0.085% trigger threshold, i.e. 6–30× over — it could never have been tuned around, and no threshold change would fix it without also blinding the detector to real darts. Fix is structural, in `DetectionSession._flash_suppressed`: arm `_suppress_until` when the flash fires, drop every frame shot during the flash window (`FLASH_SUPPRESS_SECONDS` = flash duration + 0.35s settle, so the revert-to-white is covered too), then force a baseline relearn on the tick it ends — the stored baseline was captured under the *pre-flash* lighting and the just-thrown dart is now part of the scene, so a fresh reference under restored light is needed either way. This supersedes the normal `cooldown` state, which does the same job less well here. Regression-tested by reproducing the phantom trigger and confirming suppression prevents it.

- **Making takeout detection reliable — the detector now knows the engine is waiting.** Reported as working "2 times out of 3". Two structural causes, neither fixed by tuning a threshold:
  1. **Pulling a dart out looks identical to throwing one in** — one dart-shaped patch of changed pixels either way. So a takeout would settle, fuse into a *confident hit*, get handed to the engine, and be dropped there for having no darts left in the turn. From the board it looked like nothing happened at all. The fix is to use information the detector already had access to but never asked for: once `awaiting_takeout` is set the turn is full, **no dart can be scored**, and the hardest question in the pipeline ("dart or hand?") collapses to one answer. A settled event during that window is now unconditionally a takeout, and the large-change bar drops from 12% to 2% (a hand reaching in from one side doesn't always sweep 12% of two cameras). Mid-turn behaviour is completely unchanged — the same 4% change is still settled as a possible dart.
  2. **Darts come out one at a time.** Pulling the first fired the takeout and advanced the turn; pulling the second and third then looked like the *next player throwing*, which either scored phantom darts or fired a second takeout and **skipped a player** — the exact symptom the confirm-takeout override was built to repair. A takeout now enters a `clearing` state that scores nothing and fires no further takeout until the board has been genuinely still for 0.8s, then relearns the baseline with the board empty. A hard 10s timeout stops someone leaning on the board wedging it shut. This is the "scene clear wait" the detection notes above flagged as worth porting once a game engine existed to report into.
  - Verified by driving the real `DetectionSession` state machine with synthetic frames (the live server owns the cameras): the sensitivity switch in both directions, a settled event becoming a takeout rather than a phantom dart, `clearing` swallowing repeated movement without a second takeout, the return to baseline only after stillness, and the timeout release.
  - **Takeout is now observable on the Detection page**, because it is the one event with nothing to show for it — no dart, no marker — so when it misfired or didn't fire there was nothing to look at, and tuning it was guesswork. `DetectionSession` keeps a 25-entry takeout history (`reason`, cameras, board occupancy at the time, and **whether the game was actually waiting**) exposed through `/api/detection/status` alongside a live `game_awaiting_takeout` flag, so the panel is populated on page load rather than only from live events. The page shows each takeout tagged *turn was over* or *mid-turn* — a mid-turn takeout is the signature of a false fire — plus chips for the waiting and `clearing` states and a flash when one lands. Broadcast and polled history share an `id` so the UI can dedupe.

  *General lesson worth carrying into the game engine: anything that changes board lighting (celebration effects, per-player colours, score animations) has to coordinate with the detector, not just fire and forget. `flash_cue` + suppression is the pattern; a long celebration effect will need the same treatment or detection will be unusable during it.*

- **Takeout gets its own red flash and sound — built.** Darts being pulled out was already detected three separate ways (a hand-sized change while watching, an event that analysed as a whole-board change, and a fall in board occupancy), but each path hand-rolled the same broadcast/notify/relearn trio, which is how they drifted apart. They now all go through one `DetectionSession._handle_takeout(camera_ids, reason)`, which broadcasts `detection.takeout` (now carrying *why*), fires a red `takeout` cue for 0.5s, advances the turn and relearns the baseline.
  - **Suppression matters more here than for a scored dart.** A takeout always triggers a baseline relearn, so without blinding, the reference would be captured under *red* light and the revert to white would immediately read as a phantom throw. `_suppress_until` is armed before the relearn is scheduled, and the main loop's flash gate sits above the state dispatch so it covers `_learn_baseline` too — learning only begins once the board is back to calibrated white.
  - **One event, one cue — a bug found by tracing, not testing.** `_notify_game_takeout()` calls `next_turn()`, which fired its own `turn.start` blue comet; the red flash would have been overwritten milliseconds after it started. `next_turn(cue=False)` is now used on the takeout path only, so the manual Next-player button still cues normally. Both behaviours are covered by tests.
  - **Sounds are separated by contour, not pitch** (`frontend/src/sound.js`): rising two-note = scored, flat low tone = check this dart, falling three-note run = darts out. Contour survives a noisy room far better than timbre. The play screen also shows a brief red "DARTS REMOVED" banner for anyone watching the screen rather than the board.
  - Remember the `settings_store._merge_defaults` gotcha: the new `takeout` cue had to be added to **both** `DEFAULTS` and the persisted `config/settings.json`, or the saved file would keep winning and the cue would silently not exist. Tested against both.
  - *Operational note found while testing:* fine-tuning with darts still in the board measured ~1.3mm mean ring error across the three cameras, versus ~0.65mm once they were pulled — a dart hides part of the very ring being measured. The calibration page now says to clear the board first, and on the clean run camera 4 correctly **declined** to save (0.68→0.70mm), which is the improvement guard doing its job.

**Gotcha worth remembering:** `settings_store._merge_defaults` lets values saved in `config/settings.json` win over `DEFAULTS`, so changing a cue default alone does nothing once settings have ever been saved — the persisted file needs updating too (this silently no-op'd the first attempt at the white/green cues above).

Still to do: wire actual `led_controller.cue(...)` calls into the calibration wizard and game engine once those exist (Phases 1 and 3 below), and revisit the remaining placeholder cue→effect mappings on real hardware.

## Distribution & self-update — built

The app is given to friends and family as an installer, and updates itself
from a public-read prefix on a private S3 bucket. Full detail in
[DISTRIBUTION.md](DISTRIBUTION.md); the decisions worth recording here:

- **Source app + bundled runtime, not a frozen binary.** The payload stays
  plain `.py` and built static files, with the interpreter shipped alongside
  in `runtime/`. A PyInstaller-style bundle would make every update a
  ~200 MB re-download of an executable that is 95% OpenCV and numpy; keeping
  the code as source means an update is only the files that changed.
- **Content-addressed blobs are what make updates small.** Every file is
  stored under its own SHA-256, so a file that did not change has the same
  hash, is already on disk, and costs nothing. Measured on a real installed
  copy: **3,211 bytes** for a genuine two-file update against a 4.2 MB
  payload. Uploads are idempotent and never overwrite, so publishing cannot
  disturb a release someone is already running.
- **Signing, not access control, is the trust anchor.** The bucket being
  private for writes is not enough: the failure that matters is someone
  *else* gaining write access, since every install runs whatever that path
  serves. Manifests and channel pointers are Ed25519-signed with a key held
  only on the release machine, verified against a public key compiled into
  the app. It fails **closed** — no configured key means no updates, never
  unverified ones. The channel pointer is signed separately and pins its
  manifest by hash, so a valid-but-older release cannot be swapped in to
  re-expose a fixed bug.
- **`config/` was moved outside the updatable payload.** This was a genuine
  prerequisite, not tidying: the three stores anchored `config/` to
  `backend/`'s parent, which in the installed layout is the directory an
  update replaces wholesale. Calibration, players and selfies would have
  been destroyed by every update. `backend/paths.py` is now the single place
  that knows the layout, and resolves the dev checkout and installed tree
  differently (env vars > install marker > checkout).
- **The swap happens at launch, not live.** `installer/launcher.py` sits
  outside `app/` and applies a staged update *before* starting the server —
  the code doing the swap must not live in the directory being swapped, and
  doing it before startup sidesteps Windows file locking rather than racing
  it. The previous version is kept, and a new version that dies within 25s
  is rolled back automatically.
- **Two Windows-specific bugs found only by testing on a real installed
  tree, both of which would have broken every update:** the server ran with
  its working directory *inside* `app/`, and Windows will not rename a
  directory that is any process's cwd (`Access is denied` on every update);
  and killing the launcher orphaned uvicorn, which then held `app/`, the
  cameras and the port, making the next update fail the same way. Fixed by
  running from the install root with `PYTHONPATH`, and by a Job Object with
  `KILL_ON_JOB_CLOSE`. *A third was self-inflicted and instructive: the
  first Job Object attempt silently did nothing because ctypes defaults a
  return value to C `int` and truncated the 64-bit handle — every call
  reported success. Caught only because the test checked for surviving
  processes instead of trusting the API's return value.*
- **Beta channel before stable.** `--promote` moves a channel pointer to a
  version already in the bucket, transferring nothing, so what the family
  receives is byte-for-byte what was tested rather than a rebuild of it.
- Verified end to end twice: at unit level against a local server (21
  checks, including signature forgery, payload tampering and 15 path
  traversal attempts, all rejected), and against a **real installed copy
  running the bundled runtime** (15 checks: check → download → restart →
  swap → v0.2.0 live, with user config intact and the previous version kept).

## Cross-platform (Pi 5 + Windows)

- One codebase; platform differences isolated in a `platform.py` / config layer: camera device enumeration, default resolutions/fps, performance profile (Pi may run detection at lower resolution or fps).
- Pi 5 notes: use 3 cameras on separate USB controllers where possible (bandwidth); MJPG capture format to keep USB bandwidth down; consider `picamera2` only if you later switch to CSI cameras — USB keeps parity with Windows.
- Dev loop: develop and run the dart-lab regression on Windows; deploy to Pi (systemd service) and re-run the same regression there to catch performance differences.

## Repository layout

```
claude-plan/
├── backend/
│   ├── app.py                # FastAPI entry, static file serving, all /api routes, /ws
│   ├── events.py             # Hub — WebSocket pub/sub, first slice of the delivery layer (built)
│   ├── network.py            # LAN IP discovery for the phone-join QR code (built)
│   ├── display.py            # main-screen presentation/fullscreen flag, phone-drivable (built)
│   ├── settings_store.py     # persisted app settings (config/settings.json)
│   ├── capture/               # camera enumeration + threaded capture (built)
│   │   ├── devices.py
│   │   └── manager.py
│   ├── leds/                  # led_controller hook + transports (built)
│   │   ├── controller.py      # led_controller singleton — the app-wide LED hook
│   │   ├── transport.py       # SerialTransport / HttpTransport
│   │   └── effects.py         # effect name <-> id table, kept in sync with firmware
│   ├── players/                # shared player roster (built)
│   │   ├── store.py            # config/players.json + config/selfies/ persistence
│   │   └── routes.py           # CRUD + selfie upload, broadcasts over the Hub
│   ├── calibration/            # per-camera homography calibration (built)
│   │   ├── board_model.py      # standard board geometry, no camera involved
│   │   ├── auto_detect.py      # color+Fourier-phase best-effort seed
│   │   ├── store.py            # 5 points -> homography -> config/calibration.json
│   │   └── routes.py           # auto/manual-points/grid API, drives the manual UI
│   ├── detection/              # multi-camera axis-fusion pipeline (built; ported, see below)
│   │   ├── axis.py              # per-camera RANSAC dart-axis fit (not a single tip point)
│   │   ├── fusion.py            # intersect 2-3 cameras' axes -> landing point + confidence
│   │   ├── models.py            # AxisCandidate / PairIntersection / FusedHit
│   │   ├── scoring.py           # board mm -> segment/ring/value (OUT ring, wire distance)
│   │   ├── session.py           # DetectionSession - multi-camera state machine
│   │   └── pipeline.py          # DetectionManager - thin start/stop wrapper around one session
│   ├── games/                  # engine core + one module per game (Phase 3)
│   └── dartlab/                # recorder, labeller, replay runner (Phase 2)
├── led-controller/            # ESP32 firmware, PlatformIO/Arduino (built)
│   ├── src/main.cpp            # boot, non-blocking WiFi state machine, main loop
│   ├── src/serial_ctrl.cpp     # USB serial JSON protocol
│   ├── src/api.cpp             # WiFi HTTP REST API
│   ├── src/state_json.cpp      # shared JSON<->LedState, used by both transports
│   └── src/effects.cpp         # 41 LED effects (FastLED)
├── frontend/                  # Vue 3 + Vite SPA (main + phone layouts)
│   ├── src/views/               # Home (QR), Players, Setup, Calibration, Leds, Join (built); games later
│   ├── src/components/          # PlayerRoster, AvatarPicker, SelfieCapture,
│   │                             # CalibrationGrid (SVG overlay), ManualCalibration,
│   │                             # DartboardFace, SpaceStage (alien fleet + cannon),
│   │                             # BoardPicker (clickable board) (built)
│   ├── src/stores/players.js    # Pinia store, live-synced over /ws (built)
│   ├── public/avatars/          # generated default avatar gallery (built)
│   └── public/arenas/           # per-game arena backdrops for the play screens
├── tools/
│   ├── generate_avatars.py    # generates the avatar gallery (placeholder mascots — see below)
│   └── generate_dev_cert.py   # self-signed HTTPS cert, needed for phone camera access (see below)
├── clips/                     # recorded footage + ground truth labels (Phase 2)
├── config/                    # settings/players.json, selfies/, calibration profiles (git-ignored)
└── deploy/                    # Pi systemd unit, install script (later)
```

## Phased build plan

**Phase 0 — Rig & capture (foundation) — mostly built**
Camera enumeration (Windows DirectShow + Linux v4l2), threaded ref-counted capture with MJPEG live preview, Setup page with 3 camera slots and persisted selection — done and tested with real webcams. LED surround also built as a side detour (see above): ESP32 firmware with dual serial/WiFi transport, `led_controller` backend hook, LEDs config/test page. *Remaining: mount the real board cameras and confirm stable capture at target fps on the Pi 5 itself (only tested on Windows so far).*

**Phase 1 — Calibration — built**
Board model, homography calibration with auto-detect seeding + manual point-confirm UI (with magnifier), profile persistence — done, see Calibration section above. *Remaining for full exit: click-on-image → predicted score sanity-check tool (trivial now that the homography + inverse-projection machinery exists — just needs a "click a point, show segment/ring" debug view), and live end-to-end testing of the manual UI on real hardware (verified thoroughly at the API/logic level; the full-screen click-and-drag flow itself hasn't been exercised in a real browser, since no browser automation is available here).*

**Phase 2 — Detection + dart lab — multi-camera axis-fusion pipeline built**
Rebuilt (not patched) on a fundamentally different technique after a single-camera tip-finding architecture proved unreliable on real throws even after fixing its noise-sensitivity bug — see Detection pipeline section above for the full story and why axis-fitting + multi-camera line intersection is better-founded than per-camera tip guessing. Axis detection, fusion, the multi-camera session state machine, and an updated `/detection` debug UI (live axis-line overlay per camera, fused hit panel, event log) are built and verified against real cameras + real calibration data, thoroughly at the synthetic/unit level. *Remaining for full exit: actual real-throw validation (the axis/fusion math is proven, but hasn't watched a real sequence of real darts yet), and the dart lab itself (recorder, ground-truth labeller, replay runner) to turn "throw and eyeball it" into the measurable offline regression loop PLAN.md always intended. Target: ≥95% correct segment+ring on the clip library, fused across cameras.*

**Phase 3 — Core engine + x01**
Event-sourced engine, game registry, x01 with double-out, correction/undo flow end-to-end with live detection. Player roster (who's playing) is already built — see Players engine above. *Exit: a full 501 match scored automatically with occasional manual corrections.*

**Phase 4 — Web UIs proper — well underway**
Home screen QR code (built: `GET /api/network/info` finds the LAN IP so the code works regardless of what host the main screen's own browser is using). Phone GUI player registration + selfies (built, see Players engine above). Also fixed while doing this: FastAPI's static file serving only resolved `/` correctly for Vue Router's history mode — added an SPA fallback route so any client-side path (`/join`, `/players`, ...) resolves when served from the production build. Remaining: correction overlay on board SVG, live game screen, main GUI polish — all depend on the game engine (Phase 3). *Exit: two phones + main screen in one game session.*

**Phase 5 — Game library + LED polish**
Add games one per iteration using the plugin interface; LED effect mapping; themed per-game graphics. *Exit: 4+ games playable.*

Your instinct is right: Phases 0–2 are the foundation and the risk. Everything from Phase 3 onward is conventional software; detection accuracy is the make-or-break, which is why the dart lab (measurable, offline tuning) comes before any game code.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| USB bandwidth on Pi 5 with 3 cameras | MJPG format, moderate resolution (1280×720 is plenty for tip localisation), separate controllers |
| Lighting changes break detection | Rolling background model; a small LED ring light on the board rig; calibration health check |
| Occlusion (dart hides dart) | 3-camera voting; confidence flag → one-tap correction UI |
| Board moves / cameras knocked | Startup reprojection check + quick recalibrate flow |
| Detection tuning is endless | Dart-lab regression gives an objective score; stop when the clip-library target is met |
