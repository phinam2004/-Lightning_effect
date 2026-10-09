"""
hand_tracking.py
-----------------
Wraps MediaPipe's modern Tasks API (HandLandmarker) for real-time hand
tracking, and classifies raw landmarks into the gesture vocabulary used
by the Chaos Magic effect system:

    - open_palm   : palm open, facing camera, fingers relaxed/spread
    - claw        : fingers curling inward toward a point (not a full fist)
    - fist        : fully closed fist
    - push        : fast forward thrust of an otherwise open/relaxed hand
    - neutral     : none of the above (hand present but not casting)

Landmarks follow the standard 21-point MediaPipe hand model:
    0  wrist
    1-4   thumb  (CMC, MCP, IP, TIP)
    5-8   index  (MCP, PIP, DIP, TIP)
    9-12  middle (MCP, PIP, DIP, TIP)
    13-16 ring   (MCP, PIP, DIP, TIP)
    17-20 pinky  (MCP, PIP, DIP, TIP)

All geometry helpers work on normalized (x, y, z) landmark coordinates
as returned by MediaPipe (x, y in [0, 1] relative to image, z relative
depth, smaller/more negative = closer to camera).
"""

from __future__ import annotations

import os
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional

import numpy as np

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    _HAS_TASKS_API = hasattr(mp_vision, "HandLandmarker")
except Exception:  # pragma: no cover - defensive import guard
    mp = None
    _HAS_TASKS_API = False

# Fallback to legacy mp.solutions.hands only if the modern Tasks API is
# genuinely unavailable in the installed mediapipe build.
_HAS_LEGACY_API = False
if not _HAS_TASKS_API:
    try:
        import mediapipe as mp  # noqa: F401  (re-import for clarity)
        _HAS_LEGACY_API = hasattr(mp.solutions, "hands")
    except Exception:
        _HAS_LEGACY_API = False

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
MODEL_PATH = os.path.join(os.path.dirname(__file__), "hand_landmarker.task")

# Landmark index constants for readability.
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

FINGER_CHAINS = [
    (INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP),
    (MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP),
    (RING_MCP, RING_PIP, RING_DIP, RING_TIP),
    (PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP),
]


def _ensure_model_downloaded() -> str:
    """Download the MediaPipe hand landmarker model if it isn't cached
    locally yet. Returns the local path to the .task model file."""
    if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 0:
        return MODEL_PATH
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    except Exception as exc:  # pragma: no cover - network dependent
        raise RuntimeError(
            "Could not download hand_landmarker.task automatically. "
            "Please download it manually from:\n  " + MODEL_URL +
            "\nand place it next to hand_tracking.py as 'hand_landmarker.task'."
        ) from exc
    return MODEL_PATH


@dataclass
class HandObservation:
    """A single tracked hand for the current frame."""
    handedness: str                     # "Left" or "Right" (as reported, mirrored view applied by caller)
    landmarks: np.ndarray                # (21, 3) normalized x, y, z
    landmarks_px: np.ndarray             # (21, 2) pixel-space x, y
    palm_center_px: np.ndarray           # (2,) pixel-space palm centroid
    gesture: str = "neutral"
    facing_camera: bool = False
    finger_curl: np.ndarray = field(default_factory=lambda: np.zeros(4))
    push_velocity: float = 0.0           # px/sec, positive = toward camera-ish forward thrust


class _VelocityTracker:
    """Tracks recent wrist positions/depth per hand id to detect a fast
    forward 'push' thrust gesture."""

    def __init__(self, history: int = 6):
        self.history = history
        self.buffers: dict[str, Deque[tuple]] = {}

    def update(self, key: str, px: np.ndarray, z: float, t: float) -> float:
        buf = self.buffers.setdefault(key, deque(maxlen=self.history))
        buf.append((t, px.copy(), z))
        if len(buf) < 2:
            return 0.0
        t0, p0, z0 = buf[0]
        t1, p1, z1 = buf[-1]
        dt = max(t1 - t0, 1e-3)
        # Forward thrust shows up as: hand moving toward camera (z decreasing,
        # MediaPipe z is negative-toward-camera) combined with planar speed.
        depth_speed = (z0 - z1) / dt          # positive = moving toward camera
        planar_speed = float(np.linalg.norm(p1 - p0)) / dt
        # Blend both signals into a single forward-push score.
        return max(depth_speed * 4.0, 0.0) + planar_speed * 0.5


def _finger_curl_amount(lm: np.ndarray, chain: tuple) -> float:
    """Returns a 0..1 curl amount for one finger: 0 = fully extended,
    1 = fully curled, based on the angle at the PIP joint."""
    mcp, pip, dip, tip = (lm[i] for i in chain)
    v1 = pip - mcp
    v2 = tip - pip
    n1 = np.linalg.norm(v1) + 1e-6
    n2 = np.linalg.norm(v2) + 1e-6
    cos_angle = np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)
    angle = np.arccos(cos_angle)  # 0 = straight, pi = fully folded back
    # Also fold in tip-to-mcp distance vs pip-to-mcp distance: a curled
    # finger brings the tip much closer to the palm.
    straight_reach = np.linalg.norm(tip - mcp)
    max_reach = n1 + n2
    reach_ratio = straight_reach / (max_reach + 1e-6)  # 1 = straight, <1 = curled
    curl_from_angle = np.clip(angle / (np.pi * 0.65), 0.0, 1.0)
    curl_from_reach = np.clip(1.0 - reach_ratio, 0.0, 1.0)
    return float(0.5 * curl_from_angle + 0.5 * curl_from_reach)


def _classify_static_gesture(lm: np.ndarray) -> tuple[str, np.ndarray, bool]:
    """Classify the static hand pose (ignoring motion) into
    open_palm / claw / fist / neutral, and estimate whether the palm
    is facing the camera."""
    curls = np.array([_finger_curl_amount(lm, chain) for chain in FINGER_CHAINS])
    mean_curl = float(curls.mean())

    # Facing-camera heuristic: for a palm facing the camera, the finger
    # MCP knuckles form a plane whose normal points roughly toward the
    # viewer; approximate via the cross product of two in-palm vectors
    # and check the sign/magnitude of its z component versus wrist depth.
    wrist = lm[WRIST]
    index_mcp = lm[INDEX_MCP]
    pinky_mcp = lm[PINKY_MCP]
    v_a = index_mcp - wrist
    v_b = pinky_mcp - wrist
    normal = np.cross(v_a, v_b)
    facing_camera = normal[2] < 0  # heuristic sign depends on handedness/mirroring upstream

    if mean_curl < 0.28:
        gesture = "open_palm"
    elif mean_curl > 0.78:
        gesture = "fist"
    elif 0.35 < mean_curl < 0.75:
        gesture = "claw"
    else:
        gesture = "neutral"

    return gesture, curls, facing_camera


class HandTracker:
    """High level tracker: feed BGR frames in, get back a list of
    HandObservation per detected hand (max 2), each already classified."""

    def __init__(self, max_hands: int = 2, min_detection_confidence: float = 0.6,
                 min_tracking_confidence: float = 0.6):
        self.max_hands = max_hands
        self._velocity = _VelocityTracker()
        self._backend = None
        self._legacy_hands = None

        if _HAS_TASKS_API:
            model_path = _ensure_model_downloaded()
            base_options = mp_python.BaseOptions(model_asset_path=model_path)
            options = mp_vision.HandLandmarkerOptions(
                base_options=base_options,
                running_mode=mp_vision.RunningMode.VIDEO,
                num_hands=max_hands,
                min_hand_detection_confidence=min_detection_confidence,
                min_hand_presence_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
            self._backend = "tasks"
        elif _HAS_LEGACY_API:
            self._legacy_hands = mp.solutions.hands.Hands(
                max_num_hands=max_hands,
                min_detection_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            self._backend = "legacy"
        else:
            raise RuntimeError(
                "No usable MediaPipe hand-tracking API found. Please install "
                "a mediapipe version that provides either "
                "mediapipe.tasks.python.vision.HandLandmarker or "
                "mediapipe.solutions.hands."
            )

    def close(self):
        if self._backend == "tasks":
            self._landmarker.close()
        elif self._backend == "legacy":
            self._legacy_hands.close()

    def process(self, frame_bgr: np.ndarray) -> List[HandObservation]:
        """Run detection on one mirrored BGR frame and return classified
        hand observations."""
        h, w = frame_bgr.shape[:2]
        now = time.time()
        observations: List[HandObservation] = []

        if self._backend == "tasks":
            import cv2
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            timestamp_ms = int(now * 1000)
            result = self._landmarker.detect_for_video(mp_image, timestamp_ms)
            hands_landmarks = result.hand_landmarks
            handedness_list = result.handedness
            for i, hand_lms in enumerate(hands_landmarks):
                lm = np.array([[p.x, p.y, p.z] for p in hand_lms], dtype=np.float32)
                label = "Hand"
                if i < len(handedness_list) and len(handedness_list[i]) > 0:
                    label = handedness_list[i][0].category_name
                observations.append(self._build_observation(label + f"_{i}", lm, w, h, now))

        elif self._backend == "legacy":
            import cv2
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            result = self._legacy_hands.process(rgb)
            if result.multi_hand_landmarks:
                for i, hand_lms in enumerate(result.multi_hand_landmarks):
                    lm = np.array([[p.x, p.y, p.z] for p in hand_lms.landmark], dtype=np.float32)
                    label = "Hand"
                    if result.multi_handedness and i < len(result.multi_handedness):
                        label = result.multi_handedness[i].classification[0].label
                    observations.append(self._build_observation(label + f"_{i}", lm, w, h, now))

        return observations

    def _build_observation(self, key: str, lm: np.ndarray, w: int, h: int, now: float) -> HandObservation:
        landmarks_px = np.stack([lm[:, 0] * w, lm[:, 1] * h], axis=1)
        palm_ids = [WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP]
        palm_center_px = landmarks_px[palm_ids].mean(axis=0)

        gesture, curls, facing_camera = _classify_static_gesture(lm)

        push_score = self._velocity.update(key, palm_center_px, float(lm[WRIST, 2]), now)
        is_push = push_score > 650.0 and gesture in ("open_palm", "neutral")
        if is_push:
            gesture = "push"

        return HandObservation(
            handedness=key,
            landmarks=lm,
            landmarks_px=landmarks_px,
            palm_center_px=palm_center_px,
            gesture=gesture,
            facing_camera=facing_camera,
            finger_curl=curls,
            push_velocity=push_score,
        )
