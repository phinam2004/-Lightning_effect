"""
main.py
-------
Scarlet Witch Chaos Magic -- real-time webcam VFX.

Run:
    python main.py

Controls:
    q / ESC   quit
    f         toggle on-screen FPS counter

See README.md for the full gesture vocabulary and design notes.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

import cv2
import numpy as np

from hand_tracking import HandTracker, HandObservation, INDEX_MCP, WRIST
from effects import HandMagicState
from particles import PushBoltSystem
from renderer import Renderer

CAM_INDEX = 0
REQUESTED_WIDTH = 1280
REQUESTED_HEIGHT = 720


def _palm_forward_direction(obs: HandObservation) -> np.ndarray:
    """Approximate 2D 'pointing/forward' direction for the palm, used to
    aim a push bolt: from wrist toward the middle-finger MCP knuckle."""
    wrist_px = obs.landmarks_px[WRIST]
    index_mcp_px = obs.landmarks_px[INDEX_MCP]
    d = index_mcp_px - wrist_px
    n = np.linalg.norm(d)
    if n < 1e-3:
        return np.array([0.0, -1.0], dtype=np.float32)
    return (d / n).astype(np.float32)


def main():
    cap = cv2.VideoCapture(CAM_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, REQUESTED_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, REQUESTED_HEIGHT)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open webcam at index {CAM_INDEX}.")

    ok, frame = cap.read()
    if not ok:
        raise RuntimeError("Could not read an initial frame from the webcam.")
    frame_h, frame_w = frame.shape[:2]

    tracker = HandTracker(max_hands=2)
    renderer = Renderer(frame_w, frame_h)
    bolts = PushBoltSystem()

    hand_states: Dict[str, HandMagicState] = {}

    show_fps = True
    prev_time = time.time()
    fps_smooth = 0.0

    window_name = "Chaos Magic"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    try:
        cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
    except Exception:
        pass

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)  # mirror webcam

            now = time.time()
            dt = max(now - prev_time, 1e-4)
            prev_time = now
            fps_smooth = fps_smooth * 0.9 + (1.0 / dt) * 0.1

            observations = tracker.process(frame)

            # Map raw detections (which include a per-frame index suffix)
            # to a stable per-hand key based on left/right handedness so
            # state persists across frames even if detection order shifts.
            seen_keys = set()
            obs_by_key: Dict[str, HandObservation] = {}
            for obs in observations:
                base_key = obs.handedness.split("_")[0]
                obs_by_key[base_key] = obs
                seen_keys.add(base_key)

            for key, obs in obs_by_key.items():
                if key not in hand_states:
                    hand_states[key] = HandMagicState(hand_key=key)
                forward = _palm_forward_direction(obs) if obs.gesture == "push" else None
                hand_states[key].update(
                    dt=dt,
                    gesture=obs.gesture,
                    palm_px=obs.palm_center_px.astype(np.float32),
                    frame_w=frame_w,
                    frame_h=frame_h,
                    bolts=bolts,
                    palm_forward=forward,
                )

            # Hands not seen this frame still get updated so their magic
            # eases out smoothly instead of vanishing instantly.
            for key, hs in hand_states.items():
                if key not in seen_keys:
                    hs.update(dt=dt, gesture=None, palm_px=None,
                              frame_w=frame_w, frame_h=frame_h, bolts=bolts, palm_forward=None)

            bolts.update(dt)

            active_states = [hs for hs in hand_states.values() if hs.is_visible]
            frame = renderer.render(frame, active_states, bolts, now)

            if show_fps:
                cv2.putText(frame, f"FPS: {fps_smooth:4.1f}", (16, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

            cv2.imshow(window_name, frame)
            key_pressed = cv2.waitKey(1) & 0xFF
            if key_pressed in (ord('q'), 27):
                break
            elif key_pressed == ord('f'):
                show_fps = not show_fps

    finally:
        cap.release()
        tracker.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
