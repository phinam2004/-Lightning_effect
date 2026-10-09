"""
particles.py
------------
All particle simulation for the Chaos Magic effect.

Two very different systems live here, on purpose:

1. SwirlParticleSystem
   A dense (thousands of particles), fully vectorized numpy system for the
   crimson energy swirl + embers around each casting palm. Every particle's
   physics update happens as array math -- there is no per-particle Python
   loop anywhere in `update()`.

2. DebrisSystem
   A small (10-20 items), per-item Python-loop system for telekinesis
   objects. This is intentional: object count is tiny, each object carries
   richer per-item state (tumble rotation, individual orbit wobble phase,
   pull/release animation), and a loop is the clearest, most maintainable
   way to express that -- vectorizing 15 objects buys nothing.

3. PushBoltSystem
   A short-lived vectorized traveling wave/bolt spawned by the "push"
   gesture. Small in particle count but still done with array math since
   it's spawned/despawned frequently.

All systems work in a *local* coordinate frame anchored to a hand's palm
position (in pixel space), and are explicitly capped to a small radius so
they can never bleed across the frame -- the renderer additionally clips
each system to a soft-edged circular mask around the palm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ----------------------------------------------------------------------
# Dense vectorized swirl + embers
# ----------------------------------------------------------------------

class SwirlParticleSystem:
    """A dense, GPU-shader-style particle swirl anchored to a single hand.

    Particles live in polar coordinates relative to the palm center, with
    layered noise-driven angular drift so the motion looks chaotic/organic
    rather than a clean mechanical spiral. All updates are vectorized.
    """

    def __init__(self, count: int = 2200, base_radius: float = 55.0, seed: Optional[int] = None):
        rng = np.random.default_rng(seed)
        self.count = count
        self.base_radius = base_radius

        # Polar state: radius (0..1 of current max radius), angle (radians)
        self.radius_frac = rng.uniform(0.15, 1.0, count).astype(np.float32)
        self.angle = rng.uniform(0, 2 * np.pi, count).astype(np.float32)

        # Per-particle constants that create varied, layered chaotic motion.
        self.angular_speed = rng.uniform(1.2, 3.6, count).astype(np.float32)
        self.angular_speed *= rng.choice([-1.0, 1.0], count)  # mixed spin directions
        self.noise_freq_a = rng.uniform(0.6, 1.4, count).astype(np.float32)
        self.noise_freq_b = rng.uniform(1.8, 3.2, count).astype(np.float32)
        self.noise_phase_a = rng.uniform(0, 2 * np.pi, count).astype(np.float32)
        self.noise_phase_b = rng.uniform(0, 2 * np.pi, count).astype(np.float32)
        self.noise_amp = rng.uniform(0.15, 0.55, count).astype(np.float32)

        self.radial_drift = rng.uniform(-0.06, 0.10, count).astype(np.float32)
        self.size = rng.uniform(1.0, 3.6, count).astype(np.float32)
        self.brightness = rng.uniform(0.4, 1.0, count).astype(np.float32)
        self.life = rng.uniform(0.0, 1.0, count).astype(np.float32)
        self.max_life = rng.uniform(1.2, 3.0, count).astype(np.float32)

        # Ember breakoff state: a subset of particles occasionally detach
        # and drift outward before fading, tracked with a boolean mask.
        self.is_ember = np.zeros(count, dtype=bool)
        self.ember_outward_speed = rng.uniform(30.0, 90.0, count).astype(np.float32)

        self._t = 0.0

    def update(self, dt: float, charge: float, rng: Optional[np.random.Generator] = None):
        """Vectorized physics update. `charge` in [0, 1] scales speed,
        radius and spawn rate of ember breakoff."""
        self._t += dt
        rng = rng or np.random.default_rng()

        # Layered noise angular perturbation -> organic, unstable curling motion.
        noise = (
            self.noise_amp * np.sin(self.noise_freq_a * self._t + self.noise_phase_a)
            + self.noise_amp * 0.6 * np.sin(self.noise_freq_b * self._t + self.noise_phase_b)
        )
        speed_scale = 0.4 + 1.6 * charge
        self.angle += (self.angular_speed + noise) * dt * speed_scale

        # Radius breathes in/out with its own drift + a shared charge pulse.
        pulse = 0.06 * np.sin(self._t * 2.2)
        self.radius_frac += self.radial_drift * dt * (0.5 + charge) + pulse * dt
        self.radius_frac = np.clip(self.radius_frac, 0.1, 1.0)

        # Ember particles drift outward and fade instead of orbiting.
        ember = self.is_ember
        if ember.any():
            self.radius_frac[ember] += (self.ember_outward_speed[ember] / self.base_radius) * dt

        # Life-cycle: age particles, recycle dead ones back into the swirl.
        self.life += dt
        dead = self.life >= self.max_life
        n_dead = int(dead.sum())
        if n_dead:
            self.radius_frac[dead] = rng.uniform(0.15, 0.5, n_dead).astype(np.float32)
            self.angle[dead] = rng.uniform(0, 2 * np.pi, n_dead).astype(np.float32)
            self.life[dead] = 0.0
            self.max_life[dead] = rng.uniform(1.2, 3.0, n_dead).astype(np.float32)
            self.is_ember[dead] = False

        # Randomly promote a small fraction of far-out particles to embers
        # that break away, scaled by how charged the magic currently is.
        far = (~self.is_ember) & (self.radius_frac > 0.85)
        if far.any():
            spawn_chance = 0.02 * (0.3 + charge)
            roll = rng.random(int(far.sum()))
            promote_idx = np.flatnonzero(far)[roll < spawn_chance]
            self.is_ember[promote_idx] = True

    def get_render_arrays(self, palm_px: np.ndarray, radius_px: float):
        """Returns (positions[N,2] in pixel space, size[N], alpha[N]) ready
        for vectorized scatter-splatting by the renderer."""
        r = self.radius_frac * radius_px
        x = palm_px[0] + r * np.cos(self.angle)
        y = palm_px[1] + r * np.sin(self.angle)
        positions = np.stack([x, y], axis=1)

        life_frac = np.clip(self.life / self.max_life, 0.0, 1.0)
        fade_in = np.clip(life_frac * 6.0, 0.0, 1.0)
        fade_out = np.clip((1.0 - life_frac) * 6.0, 0.0, 1.0)
        ember_fade = np.where(self.is_ember, np.clip(1.0 - self.radius_frac, 0.0, 1.0), 1.0)
        alpha = self.brightness * fade_in * fade_out * ember_fade

        # Particles nearer the core (small radius_frac) render brighter/denser
        # to create the "deep red core fading outward" look.
        core_boost = 1.0 - 0.5 * self.radius_frac
        alpha = alpha * core_boost

        return positions, self.size, alpha, self.radius_frac


# ----------------------------------------------------------------------
# Push bolt (short-lived traveling wave)
# ----------------------------------------------------------------------

class PushBolt:
    """One short-lived crimson bolt/wave traveling outward from a palm."""

    def __init__(self, origin_px: np.ndarray, direction: np.ndarray, seed: Optional[int] = None,
                 count: int = 260, speed: float = 900.0, life: float = 0.45):
        rng = np.random.default_rng(seed)
        self.origin = origin_px.astype(np.float32)
        d = direction.astype(np.float32)
        norm = np.linalg.norm(d) + 1e-6
        self.direction = d / norm
        self.speed = speed
        self.life = life
        self.age = 0.0
        self.dead = False

        self.t = rng.uniform(0.0, 1.0, count).astype(np.float32)  # position along travel, 0..1
        self.lateral = rng.normal(0.0, 10.0, count).astype(np.float32)
        self.size = rng.uniform(1.5, 4.0, count).astype(np.float32)
        self.brightness = rng.uniform(0.6, 1.0, count).astype(np.float32)

    def update(self, dt: float):
        self.age += dt
        self.t += (self.speed * dt) / 140.0  # travel progress scaled to a ~140px "unit" band
        if self.age >= self.life:
            self.dead = True

    def get_render_arrays(self):
        perp = np.array([-self.direction[1], self.direction[0]], dtype=np.float32)
        travel_px = np.clip(self.t, 0.0, 1.6) * 140.0
        base = self.origin[None, :] + self.direction[None, :] * travel_px[:, None]
        positions = base + perp[None, :] * self.lateral[:, None]
        age_frac = np.clip(self.age / self.life, 0.0, 1.0)
        alpha = self.brightness * (1.0 - age_frac) * np.clip(1.0 - np.abs(self.t - 1.0), 0.0, 1.0)
        return positions, self.size, alpha


class PushBoltSystem:
    def __init__(self):
        self.bolts: list[PushBolt] = []

    def spawn(self, origin_px: np.ndarray, direction: np.ndarray):
        self.bolts.append(PushBolt(origin_px, direction))

    def update(self, dt: float):
        for b in self.bolts:
            b.update(dt)
        self.bolts = [b for b in self.bolts if not b.dead]


# ----------------------------------------------------------------------
# Telekinesis debris (small count, per-item loop -- intentional)
# ----------------------------------------------------------------------

@dataclass
class DebrisItem:
    orbit_radius: float
    angle: float
    angular_speed: float
    wobble_phase: float
    wobble_amp: float
    tumble: float
    tumble_speed: float
    size: float
    shape_seed: int
    state: str = "outside"          # outside -> pulling -> orbiting -> releasing -> outside
    pos: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float32))
    home_pos: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float32))
    progress: float = 0.0           # 0..1 transition progress for pulling/releasing


class DebrisSystem:
    """Small-count telekinesis object system, one Python loop per frame
    over ~10-20 items -- appropriate given the low count and rich
    per-item behavior (pull-in / orbit / release animation, tumble)."""

    def __init__(self, n_items: int = 14, seed: Optional[int] = None):
        self.rng = np.random.default_rng(seed)
        self.items: list[DebrisItem] = []
        for i in range(n_items):
            self.items.append(DebrisItem(
                orbit_radius=float(self.rng.uniform(70, 130)),
                angle=float(self.rng.uniform(0, 2 * np.pi)),
                angular_speed=float(self.rng.uniform(0.8, 1.8)) * (1 if i % 2 == 0 else -1),
                wobble_phase=float(self.rng.uniform(0, 2 * np.pi)),
                wobble_amp=float(self.rng.uniform(4, 14)),
                tumble=float(self.rng.uniform(0, 360)),
                tumble_speed=float(self.rng.uniform(40, 220)) * (1 if i % 3 else -1),
                size=float(self.rng.uniform(5, 13)),
                shape_seed=i,
            ))

    def set_active(self, active: bool, palm_px: np.ndarray, frame_w: int, frame_h: int):
        """Called every frame with whether telekinesis is currently
        engaged for this hand. Handles pull-in / release transitions."""
        for item in self.items:
            if active and item.state in ("outside",):
                # Assign a home position scattered around the room (outside
                # the immediate palm radius) the first time it's pulled.
                angle = self.rng.uniform(0, 2 * np.pi)
                dist = self.rng.uniform(180, min(frame_w, frame_h) * 0.45)
                item.home_pos = palm_px + dist * np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)
                item.pos = item.home_pos.copy()
                item.state = "pulling"
                item.progress = 0.0
            elif (not active) and item.state == "orbiting":
                item.state = "releasing"
                item.progress = 0.0

    def update(self, dt: float, palm_px: np.ndarray):
        t_pull = 0.6   # seconds to pull in
        t_release = 0.8
        for item in self.items:
            item.tumble = (item.tumble + item.tumble_speed * dt) % 360.0

            if item.state == "pulling":
                item.progress = min(1.0, item.progress + dt / t_pull)
                ease = 1 - (1 - item.progress) ** 3  # ease-out cubic
                orbit_pos = palm_px + item.orbit_radius * np.array(
                    [np.cos(item.angle), np.sin(item.angle)], dtype=np.float32)
                item.pos = item.home_pos * (1 - ease) + orbit_pos * ease
                if item.progress >= 1.0:
                    item.state = "orbiting"

            elif item.state == "orbiting":
                item.angle += item.angular_speed * dt
                wobble = item.wobble_amp * np.sin(item.angle * 2.3 + item.wobble_phase)
                r = item.orbit_radius + wobble
                item.pos = palm_px + r * np.array([np.cos(item.angle), np.sin(item.angle)], dtype=np.float32)

            elif item.state == "releasing":
                item.progress = min(1.0, item.progress + dt / t_release)
                ease = item.progress
                item.angle += item.angular_speed * dt * (1 - ease)
                orbit_pos = palm_px + item.orbit_radius * np.array(
                    [np.cos(item.angle), np.sin(item.angle)], dtype=np.float32)
                drift_dir = orbit_pos - palm_px
                drift_dir = drift_dir / (np.linalg.norm(drift_dir) + 1e-6)
                outward_pos = orbit_pos + drift_dir * (140.0 * ease)
                item.pos = orbit_pos * (1 - ease) + outward_pos * ease
                if item.progress >= 1.0:
                    item.state = "outside"

    def get_render_items(self):
        """Returns list of (pos, size, tumble_deg, alpha, shape_seed) for
        items that are currently visible (not fully 'outside')."""
        out = []
        for item in self.items:
            if item.state == "outside":
                continue
            alpha = 1.0
            if item.state == "pulling":
                alpha = min(1.0, item.progress * 2.0)
            elif item.state == "releasing":
                alpha = max(0.0, 1.0 - item.progress)
            out.append((item.pos, item.size, item.tumble, alpha, item.shape_seed))
        return out
