"""
effects.py
----------
Per-hand Chaos Magic state: gesture -> state machine, charge easing,
glyph ring geometry, and the localized reality-distortion field.

Each hand gets its own HandMagicState instance so tracking or extinguishing
one hand's magic never touches the other hand's state (independent per-hand
behavior, as required).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from particles import SwirlParticleSystem, DebrisSystem, PushBoltSystem

# State machine states for a single hand's magic.
IDLE = "idle"
CASTING = "casting"
TELEKINESIS = "telekinesis"
EXTINGUISHING = "extinguishing"

MIN_SWIRL_RADIUS = 45.0
MAX_SWIRL_RADIUS = 95.0
FIST_HOLD_TIME = 1.0  # seconds a fist must be held to trigger extinguish
FLARE_DURATION = 0.55  # seconds the ignition flare burst stays visible


def _ease(current: float, target: float, dt: float, rate: float = 6.0) -> float:
    """Simple exponential ease toward target, framerate-independent."""
    alpha = 1.0 - np.exp(-rate * dt)
    return current + (target - current) * alpha


@dataclass
class HandMagicState:
    hand_key: str
    state: str = IDLE
    charge: float = 0.0                # 0..1, eased "how charged" the magic is
    swirl_radius: float = MIN_SWIRL_RADIUS
    glyph_angle: float = 0.0
    fist_hold_time: float = 0.0
    palm_px: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float32))
    last_seen: float = field(default_factory=time.time)

    swirl: SwirlParticleSystem = field(default=None)
    debris: DebrisSystem = field(default=None)

    # Ignition-flare toggle: each time this hand re-ignites out of a fist
    # (or from idle), it alternates between the plain swirl fade-in and a
    # bright radiating "flare burst" ignition (Avengers-poster style
    # starburst), so re-casting twice cycles: flare -> plain -> flare -> plain.
    sim_time: float = 0.0
    ignition_variant: int = 0          # toggles 0/1 on each ignition
    flare_start: float = -999.0        # sim_time when the current flare began
    flare_active: bool = False

    def __post_init__(self):
        if self.swirl is None:
            self.swirl = SwirlParticleSystem(seed=hash(self.hand_key) % (2 ** 31))
        if self.debris is None:
            self.debris = DebrisSystem(seed=hash(self.hand_key) % (2 ** 31))

    # ------------------------------------------------------------------
    def update(self, dt: float, gesture: Optional[str], palm_px: Optional[np.ndarray],
               frame_w: int, frame_h: int, bolts: PushBoltSystem, palm_forward: Optional[np.ndarray]):
        self.sim_time += dt
        prev_state = self.state
        present = gesture is not None
        if present:
            self.last_seen = time.time()
            self.palm_px = palm_px

        # --- state transitions -------------------------------------------------
        if not present:
            # Hand lost from view: let magic fade out gracefully rather than
            # snapping off, but drop telekinesis objects immediately-ish.
            if self.state != IDLE:
                self.state = EXTINGUISHING
        elif gesture == "fist":
            self.fist_hold_time += dt
            if self.fist_hold_time >= FIST_HOLD_TIME and self.state != IDLE:
                self.state = EXTINGUISHING
        else:
            self.fist_hold_time = 0.0
            if gesture == "claw":
                self.state = TELEKINESIS
            elif gesture in ("open_palm", "push"):
                if self.state != TELEKINESIS:
                    self.state = CASTING
            if gesture == "push" and self.state in (CASTING, TELEKINESIS) and palm_forward is not None:
                bolts.spawn(self.palm_px, palm_forward)

        if self.state == EXTINGUISHING and self.charge < 0.02:
            self.state = IDLE

        # --- ignition-flare toggle -----------------------------------------------
        # Re-igniting into an open-palm cast (from idle/extinguished) alternates
        # between a bright radiating flare-burst intro and the plain fade-in,
        # so: fist->open gives the flare look, fist->open again gives the plain
        # look, fist->open again gives the flare look, and so on.
        if self.state == CASTING and prev_state in (IDLE, EXTINGUISHING):
            self.ignition_variant = 1 - self.ignition_variant
            self.flare_start = self.sim_time
        self.flare_active = (
            self.ignition_variant == 1 and (self.sim_time - self.flare_start) < FLARE_DURATION
        )

        # --- charge target based on state --------------------------------------
        target_charge = {
            IDLE: 0.0,
            CASTING: 1.0,
            TELEKINESIS: 0.85,
            EXTINGUISHING: 0.0,
        }[self.state]
        self.charge = float(np.clip(_ease(self.charge, target_charge, dt, rate=4.5), 0.0, 1.0))

        # --- derived visual params ----------------------------------------------
        target_radius = MIN_SWIRL_RADIUS + (MAX_SWIRL_RADIUS - MIN_SWIRL_RADIUS) * self.charge
        self.swirl_radius = _ease(self.swirl_radius, target_radius, dt, rate=5.0)
        self.glyph_angle = (self.glyph_angle + dt * 35.0) % 360.0  # constant slow spin, independent of motion

        # --- sub-system updates ---------------------------------------------------
        if self.charge > 0.01:
            self.swirl.update(dt, self.charge)

        telekinesis_active = self.state == TELEKINESIS
        self.debris.set_active(telekinesis_active, self.palm_px, frame_w, frame_h)
        self.debris.update(dt, self.palm_px)

    @property
    def is_visible(self) -> bool:
        return self.charge > 0.01 or any(i.state != "outside" for i in self.debris.items)


# --------------------------------------------------------------------------
# Glyph ring geometry
# --------------------------------------------------------------------------

def glyph_ring_points(center_px: np.ndarray, radius_px: float, angle_deg: float,
                       n_sides: int = 8, ring_index: int = 0) -> np.ndarray:
    """Returns an (n_sides+1, 2) polyline for a thin rotating glyph ring
    (a simple geometric polygon-in-circle motif), one of several concentric
    rings around the palm."""
    offset = np.radians(angle_deg * (1.0 if ring_index % 2 == 0 else -1.3) + ring_index * 47.0)
    theta = np.linspace(0, 2 * np.pi, n_sides + 1) + offset
    r = radius_px * (0.55 + 0.18 * ring_index)
    x = center_px[0] + r * np.cos(theta)
    y = center_px[1] + r * np.sin(theta)
    return np.stack([x, y], axis=1)


# --------------------------------------------------------------------------
# Localized reality distortion field
# --------------------------------------------------------------------------

def build_local_distortion_maps(center_px: np.ndarray, radius_px: float, strength: float,
                                 roi_size: int, t: float) -> tuple[np.ndarray, np.ndarray, tuple]:
    """Builds small map_x/map_y remap grids for a square ROI centered on
    the hand, producing a heat-haze / lens-ripple style warp that is fully
    contained to that ROI. Returns (map_x, map_y, (x0, y0, x1, y1)) where
    the tuple is the ROI bounds in full-frame pixel coordinates.

    Vectorized (no per-pixel Python loop): builds coordinate grids with
    numpy and computes a radial sine ripple displacement across the whole
    ROI at once.
    """
    half = roi_size // 2
    x0 = int(center_px[0] - half)
    y0 = int(center_px[1] - half)
    x1 = x0 + roi_size
    y1 = y0 + roi_size

    yy, xx = np.mgrid[0:roi_size, 0:roi_size].astype(np.float32)
    cx, cy = roi_size / 2.0, roi_size / 2.0
    dx = xx - cx
    dy = yy - cy
    dist = np.sqrt(dx * dx + dy * dy) + 1e-5

    # Ripple falls off smoothly to zero at ~radius_px * 2 (distortion radius
    # never exceeds ~2x the swirl radius), using a smoothstep-like falloff.
    max_r = radius_px * 2.0
    falloff = np.clip(1.0 - dist / max_r, 0.0, 1.0)
    falloff = falloff * falloff * (3 - 2 * falloff)  # smoothstep

    ripple = np.sin(dist * 0.25 - t * 6.0) * strength * 6.0 * falloff
    inv_dist = 1.0 / dist
    disp_x = dx * inv_dist * ripple
    disp_y = dy * inv_dist * ripple

    map_x = (xx + disp_x).astype(np.float32)
    map_y = (yy + disp_y).astype(np.float32)
    return map_x, map_y, (x0, y0, x1, y1)
