# Chaos Magic — Real-Time Scarlet Witch VFX

A webcam-based visual effects project that composites localized, hand-tracked
"Chaos Magic" energy onto live camera footage using MediaPipe hand tracking —
in the spirit of Wanda Maximoff / Scarlet Witch. Every effect is confined to
a small radius around each hand; **the webcam feed itself is never tinted,
darkened, blurred, or covered.**

## Requirements

- Python 3.11
- A webcam
- Packages in `requirements.txt`

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Hand landmark model

`hand_tracking.py` uses MediaPipe's modern **Tasks API** (`HandLandmarker`),
which needs a small model file (`hand_landmarker.task`). It will be
downloaded automatically on first run. If your machine has no internet
access at runtime, download it manually and place it next to
`hand_tracking.py`:

```
https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
```

If the installed `mediapipe` build doesn't expose the Tasks API at all, the
tracker automatically falls back to the legacy `mp.solutions.hands` API.

## Run

```bash
python main.py
```

- `q` or `ESC` — quit
- `f` — toggle the FPS counter

## Gesture vocabulary

| Gesture | Effect |
|---|---|
| Open palm facing camera | Chaos Magic swirl ignites on that hand |
| Both palms open | Both hands' magic intensifies independently |
| Slow claw / grasping motion | Telekinesis: nearby debris pulled in, orbits the palm |
| Open the grasping hand back out | Debris released, drifts outward and fades |
| Fast forward thrust | Short crimson bolt/wave fires from the palm |
| Closed fist held ~1s | Magic on that hand collapses and extinguishes |

Each hand (left/right) has fully independent state — one hand can be
extinguishing while the other is mid-cast.

## Project structure

```
main.py            Webcam capture loop, orchestrates tracking -> state -> render
hand_tracking.py    MediaPipe HandLandmarker wrapper + gesture classification
effects.py          Per-hand magic state machine, glyph ring geometry, local distortion field
particles.py        Vectorized swirl/ember system, push-bolt system, small-count debris system
renderer.py         Compositor: additive glow, alpha-blended debris, localized warp, bloom
requirements.txt
```

## Design notes / how it stays localized

- **Render order** (matches the spec exactly): webcam → user → local
  distortion → telekinesis debris → swirl/embers → glyph rings + core glow.
- **Distortion** is computed only inside a small ROI (`cv2.remap` on a
  sub-array), never on the full frame, and blended back with a smoothstep
  radial mask so there's no visible seam.
- **Swirl + embers** (thousands of particles) are simulated with pure numpy
  array math — no per-particle Python loop in `SwirlParticleSystem.update()`.
  Rendering uses `np.add.at` to scatter-splat all particles into a small
  local float32 buffer in one vectorized call, then a Gaussian blur produces
  the soft HDR bloom look, and a radial mask guarantees no bleed past the
  local radius.
- **Telekinesis debris** intentionally uses a small per-item Python loop
  (10–20 objects) since each item carries richer individual state (pull-in
  easing, orbital wobble, tumble rotation) and vectorizing ~15 items has no
  performance benefit.
- **Additive blending** is used for the energy itself (it's emissive light);
  **alpha blending** is used for telekinesis debris so solid objects
  properly occlude the background instead of just glowing through it.
- **Visibility guardrail**: every effect is drawn into a small local canvas
  bounded to a soft-edged circular region around the palm before being
  composited — there is no code path that touches full-frame pixels outside
  those regions, so the webcam image can never be globally darkened, tinted,
  or blurred by this project.

## Performance

- Target: 60 FPS on a modern laptop webcam resolution (1280×720).
- All dense particle physics is vectorized numpy; only small, fixed-size
  loops remain (≤20 debris items, 2–3 glyph rings, ≤2 hands).
- Each hand keeps independent particle systems and state, so one hand's
  magic complexity never affects the other hand's update cost or behavior.
