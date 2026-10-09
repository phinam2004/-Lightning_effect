"""
renderer.py
-----------
Compositing layer. Takes the current webcam frame + per-hand magic state
and draws Chaos Magic strictly localized to small ROIs around each hand,
following the required render order:

    1. Webcam (already `frame`)
    2. User (already in `frame`, untouched)
    3. Local reality distortion (around each active hand)
    4. Telekinesis debris (orbiting objects) -- alpha blended (occludes bg)
    5. Chaos Magic swirl + embers -- additive HDR-style glow
    6. Glyph rings + palm core glow -- topmost, brightest layer

No fullscreen post-processing is ever applied: every effect operates on a
bounded sub-rectangle (ROI) around a hand and is composited back with a
soft radial mask so there are no hard edges.
"""

from __future__ import annotations

from typing import Dict, List

import cv2
import numpy as np

from effects import HandMagicState, glyph_ring_points, build_local_distortion_maps, FLARE_DURATION
from particles import PushBoltSystem

CORE_COLOR = np.array([40, 20, 200], dtype=np.float32)     # deep red (BGR)
MID_COLOR = np.array([60, 60, 235], dtype=np.float32)      # red
EDGE_COLOR = np.array([90, 140, 255], dtype=np.float32)    # orange-pink (BGR)
GLYPH_COLOR = np.array([160, 200, 255], dtype=np.float32)  # bright warm white-pink
DEBRIS_COLOR = np.array([70, 70, 90], dtype=np.float32)    # dark stone-ish fragments

FLARE_HOT_COLOR = np.array([235, 250, 255], dtype=np.float32)   # near-white hot core (BGR)
FLARE_RAY_COLOR = np.array([50, 60, 255], dtype=np.float32)     # saturated red rays

LINK_CORE_COLOR = np.array([255, 90, 20], dtype=np.float32)   # deep electric blue (BGR)
LINK_EDGE_COLOR = np.array([255, 230, 140], dtype=np.float32)  # pale cyan-white

LINK_ACTIVATE_THRESHOLD = 0.25  # both hands need at least this much charge


def _smoothstep_radial_mask(size: int) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = size / 2.0
    dist = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / c
    mask = 1.0 - np.clip(dist, 0.0, 1.0)
    mask = mask * mask * (3 - 2 * mask)
    return mask


def _fractal_lightning_path(p1: np.ndarray, p2: np.ndarray, rng: np.random.Generator,
                             iterations: int = 6, displace_scale: float = 0.55) -> np.ndarray:
    """Classic midpoint-displacement fractal lightning bolt: recursively
    displaces segment midpoints perpendicular to the segment by a shrinking
    random amount, producing a jagged forked-looking path. Each iteration
    is fully vectorized across all current segments; only the (small,
    fixed) iteration count itself is a Python loop."""
    points = np.stack([p1, p2]).astype(np.float32)
    scale = displace_scale
    for _ in range(iterations):
        seg_vec = points[1:] - points[:-1]
        seg_len = np.linalg.norm(seg_vec, axis=1, keepdims=True) + 1e-6
        perp = np.stack([-seg_vec[:, 1], seg_vec[:, 0]], axis=1) / seg_len
        mids = (points[:-1] + points[1:]) * 0.5
        disp = rng.uniform(-1.0, 1.0, len(mids)).astype(np.float32)[:, None] * seg_len * scale
        mids = mids + perp * disp

        interleaved = np.empty((len(points) + len(mids), 2), dtype=np.float32)
        interleaved[0::2] = points
        interleaved[1::2] = mids
        points = interleaved
        scale *= 0.58  # finer, subtler jaggedness at each successive level
    return points


def _spawn_lightning_branches(main_points: np.ndarray, rng: np.random.Generator,
                               n_branches: int, length_frac: float = 0.35) -> list:
    """Picks a handful of points along the main bolt and grows a shorter
    fractal side-branch from each, like real lightning forking off its
    main channel. Small fixed-count loop over branches (<=6) -- this is
    the appropriate place for a loop, not the fractal math itself."""
    branches = []
    n = len(main_points)
    if n < 4:
        return branches
    for _ in range(n_branches):
        idx = int(rng.integers(int(n * 0.15), int(n * 0.85)))
        origin = main_points[idx]
        # Local tangent direction along the main bolt, used to bias the
        # branch to fork roughly forward/sideways rather than straight back.
        prev_idx = max(idx - 1, 0)
        tangent = main_points[idx] - main_points[prev_idx]
        tnorm = np.linalg.norm(tangent) + 1e-6
        tangent = tangent / tnorm
        angle_jitter = rng.uniform(-1.3, 1.3)
        cos_a, sin_a = np.cos(angle_jitter), np.sin(angle_jitter)
        branch_dir = np.array([
            tangent[0] * cos_a - tangent[1] * sin_a,
            tangent[0] * sin_a + tangent[1] * cos_a,
        ], dtype=np.float32)
        branch_len = float(np.linalg.norm(main_points[-1] - main_points[0])) * length_frac * rng.uniform(0.4, 1.0)
        end = origin + branch_dir * branch_len
        branch_pts = _fractal_lightning_path(origin, end, rng, iterations=4, displace_scale=0.5)
        branches.append(branch_pts)
    return branches


class Renderer:
    def __init__(self, frame_w: int, frame_h: int):
        self.frame_w = frame_w
        self.frame_h = frame_h
        self._ember_trails: Dict[str, np.ndarray] = {}
        self._ember_trail_roi: Dict[str, tuple] = {}
        self._link_cache = {"next_strike_t": 0.0, "main": None, "branches": None, "p1": None, "p2": None}

    # ------------------------------------------------------------------
    def render(self, frame: np.ndarray, hand_states: List[HandMagicState],
               bolts: PushBoltSystem, t: float) -> np.ndarray:
        # Layer 3: local reality distortion, one hand at a time.
        for hs in hand_states:
            if hs.charge > 0.05:
                self._apply_local_distortion(frame, hs, t)

        # Layer 4: telekinesis debris (alpha blended, occludes background).
        for hs in hand_states:
            self._draw_debris(frame, hs)

        # Layer 5: swirl + embers (additive glow).
        for hs in hand_states:
            if hs.charge > 0.02:
                self._draw_swirl(frame, hs)

        # Push bolts share the additive swirl look but aren't tied to one hand's ROI.
        self._draw_bolts(frame, bolts)

        # Layer 6: glyph rings + palm core glow (topmost, brightest).
        for hs in hand_states:
            if hs.charge > 0.05:
                self._draw_glyphs_and_core(frame, hs)

        # Ignition flare-burst: on every *other* re-ignition from a fist,
        # a brief bright radiating starburst plays over the swirl as it
        # spins up (alternates with the plain fade-in each re-cast).
        for hs in hand_states:
            if hs.flare_active:
                self._draw_ignition_flare(frame, hs)

        # Extra layer: crackling blue energy tether between both hands, only
        # when both are charged. Still fully localized -- it's drawn into a
        # thin bounding box that hugs the palm-to-palm path, never the
        # full frame.
        self._draw_hand_link(frame, hand_states, t)

        return frame

    # ------------------------------------------------------------------
    def _apply_local_distortion(self, frame: np.ndarray, hs: HandMagicState, t: float):
        roi_size = int(hs.swirl_radius * 4.2)
        roi_size = max(40, min(roi_size, min(self.frame_w, self.frame_h) - 2))
        map_x, map_y, (x0, y0, x1, y1) = build_local_distortion_maps(
            hs.palm_px, hs.swirl_radius, hs.charge, roi_size, t)

        # Clip ROI to frame bounds; skip if fully offscreen.
        cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x1, self.frame_w), min(y1, self.frame_h)
        if cx1 <= cx0 or cy1 <= cy0:
            return

        sub = frame[cy0:cy1, cx0:cx1]
        # Adjust the map grids for the (possibly cropped) sub-region offset.
        off_x, off_y = cx0 - x0, cy0 - y0
        sub_map_x = map_x[off_y:off_y + (cy1 - cy0), off_x:off_x + (cx1 - cx0)] - off_x
        sub_map_y = map_y[off_y:off_y + (cy1 - cy0), off_x:off_x + (cx1 - cx0)] - off_y

        warped = cv2.remap(sub, sub_map_x, sub_map_y, interpolation=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_REPLICATE)

        # Soft-edge blend the warped region back in so there's no hard seam.
        mask = _smoothstep_radial_mask(roi_size)[off_y:off_y + (cy1 - cy0), off_x:off_x + (cx1 - cx0)]
        mask = (mask * min(1.0, hs.charge * 1.3))[..., None]
        frame[cy0:cy1, cx0:cx1] = (sub.astype(np.float32) * (1 - mask) +
                                    warped.astype(np.float32) * mask).astype(np.uint8)

    # ------------------------------------------------------------------
    def _draw_swirl(self, frame: np.ndarray, hs: HandMagicState):
        radius_px = hs.swirl_radius
        roi_size = int(radius_px * 3.0)
        roi_size = max(30, roi_size)
        half = roi_size // 2
        cx, cy = hs.palm_px

        positions, sizes, alphas, radius_frac = hs.swirl.get_render_arrays(hs.palm_px, radius_px)

        # Local canvas in float32 for additive HDR-style accumulation.
        canvas = np.zeros((roi_size, roi_size, 3), dtype=np.float32)
        local_x = positions[:, 0] - (cx - half)
        local_y = positions[:, 1] - (cy - half)
        xi = np.round(local_x).astype(np.int32)
        yi = np.round(local_y).astype(np.int32)
        valid = (xi >= 0) & (xi < roi_size) & (yi >= 0) & (yi < roi_size) & (alphas > 0.01)
        xi, yi = xi[valid], yi[valid]
        a = alphas[valid]
        rf = radius_frac[valid]
        sizes_v = sizes[valid]

        # Color gradient: deep red core -> orange-pink edge, vectorized lerp.
        rf_c = rf[:, None]
        color = CORE_COLOR[None, :] * (1 - rf_c) + EDGE_COLOR[None, :] * rf_c
        contrib = color * (a[:, None] * 0.9) * (sizes_v[:, None] / 3.0)

        if len(xi) > 0:
            np.add.at(canvas, (yi, xi), contrib)

        # Soft bloom: blur the accumulation buffer (vectorized OpenCV call,
        # not a per-particle loop) so points become soft glowing blobs.
        canvas = cv2.GaussianBlur(canvas, (0, 0), sigmaX=max(1.0, radius_px * 0.045))

        # --- ember trail: separate buffer with slow decay for motion blur on embers only
        ember_mask = hs.swirl.is_ember
        trail_key = hs.hand_key
        trail = self._ember_trails.get(trail_key)
        if trail is None or trail.shape[0] != roi_size:
            trail = np.zeros((roi_size, roi_size, 3), dtype=np.float32)
        trail *= 0.78  # decay -> short streak, "tiny motion blur"

        if ember_mask.any() and len(xi) > 0:
            evalid = ember_mask[valid]
            if evalid.any():
                np.add.at(trail, (yi[evalid], xi[evalid]), contrib[evalid] * 0.6)
        self._ember_trails[trail_key] = trail

        canvas = canvas + trail

        # Confine to a soft circular mask so nothing bleeds past the local radius.
        mask = _smoothstep_radial_mask(roi_size)
        canvas *= mask[..., None]

        self._composite_additive(frame, canvas, int(cx - half), int(cy - half))

    # ------------------------------------------------------------------
    def _draw_bolts(self, frame: np.ndarray, bolts: PushBoltSystem):
        for bolt in bolts.bolts:
            positions, sizes, alphas = bolt.get_render_arrays()
            xs = positions[:, 0]
            ys = positions[:, 1]
            x0, x1 = int(max(xs.min() - 20, 0)), int(min(xs.max() + 20, self.frame_w))
            y0, y1 = int(max(ys.min() - 20, 0)), int(min(ys.max() + 20, self.frame_h))
            if x1 <= x0 or y1 <= y0:
                continue
            w, h = x1 - x0, y1 - y0
            canvas = np.zeros((h, w, 3), dtype=np.float32)
            xi = np.round(xs - x0).astype(np.int32)
            yi = np.round(ys - y0).astype(np.int32)
            valid = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h) & (alphas > 0.01)
            if valid.any():
                color = MID_COLOR[None, :]
                contrib = color * (alphas[valid][:, None]) * (sizes[valid][:, None] / 3.0)
                np.add.at(canvas, (yi[valid], xi[valid]), contrib)
                canvas = cv2.GaussianBlur(canvas, (0, 0), sigmaX=2.5)
                self._composite_additive(frame, canvas, x0, y0)

    # ------------------------------------------------------------------
    def _draw_debris(self, frame: np.ndarray, hs: HandMagicState):
        items = hs.debris.get_render_items()
        if not items:
            return
        overlay = frame.copy()
        for pos, size, tumble_deg, alpha, shape_seed in items:
            rng = np.random.default_rng(shape_seed + 9001)
            n_pts = 6
            base_angles = np.linspace(0, 2 * np.pi, n_pts, endpoint=False)
            radii = size * rng.uniform(0.65, 1.0, n_pts)
            ang = base_angles + np.radians(tumble_deg)
            pts_x = pos[0] + radii * np.cos(ang)
            pts_y = pos[1] + radii * np.sin(ang) * 0.75  # slight squash for a tumbling look
            pts = np.stack([pts_x, pts_y], axis=1).astype(np.int32)
            shade = 0.6 + 0.4 * np.cos(np.radians(tumble_deg))
            color = tuple(int(c * shade) for c in DEBRIS_COLOR.tolist())
            cv2.fillPoly(overlay, [pts], color, lineType=cv2.LINE_AA)
            cv2.polylines(overlay, [pts], True, (30, 25, 40), 1, cv2.LINE_AA)

            item_alpha = float(np.clip(alpha, 0.0, 1.0))
            x0, y0 = int(pos[0] - size - 2), int(pos[1] - size - 2)
            x1, y1 = int(pos[0] + size + 2), int(pos[1] + size + 2)
            x0c, y0c = max(x0, 0), max(y0, 0)
            x1c, y1c = min(x1, self.frame_w), min(y1, self.frame_h)
            if x1c <= x0c or y1c <= y0c:
                continue
            frame[y0c:y1c, x0c:x1c] = cv2.addWeighted(
                overlay[y0c:y1c, x0c:x1c], item_alpha, frame[y0c:y1c, x0c:x1c], 1 - item_alpha, 0)

    # ------------------------------------------------------------------
    def _draw_glyphs_and_core(self, frame: np.ndarray, hs: HandMagicState):
        radius_px = hs.swirl_radius
        roi = int(radius_px * 2.6)
        half = roi // 2
        cx, cy = hs.palm_px
        canvas = np.zeros((roi, roi, 3), dtype=np.float32)
        local_center = np.array([half, half], dtype=np.float32)

        for ring_i in range(3):
            pts = glyph_ring_points(local_center, radius_px, hs.glyph_angle, n_sides=8, ring_index=ring_i)
            pts_i = pts.astype(np.int32)
            thickness = 1
            line_canvas = np.zeros_like(canvas)
            cv2.polylines(line_canvas, [pts_i], True, tuple(GLYPH_COLOR.tolist()), thickness, cv2.LINE_AA)
            # small glyph nodes at each vertex for a rune-like feel
            for p in pts_i[:-1]:
                cv2.circle(line_canvas, tuple(p), 2, tuple(GLYPH_COLOR.tolist()), -1, cv2.LINE_AA)
            canvas += line_canvas * (0.85 * hs.charge)

        # Bright palm core glow: small tight additive blob at the center.
        core_canvas = np.zeros_like(canvas)
        cv2.circle(core_canvas, tuple(local_center.astype(np.int32)),
                   max(3, int(radius_px * 0.22)), tuple((CORE_COLOR * 1.4).tolist()), -1, cv2.LINE_AA)
        core_canvas = cv2.GaussianBlur(core_canvas, (0, 0), sigmaX=max(2.0, radius_px * 0.12))
        canvas += core_canvas * hs.charge

        mask = _smoothstep_radial_mask(roi)
        canvas *= mask[..., None]

        self._composite_additive(frame, canvas, int(cx - half), int(cy - half))

    # ------------------------------------------------------------------
    def _draw_ignition_flare(self, frame: np.ndarray, hs: HandMagicState):
        """Bright radiating starburst played briefly when a hand re-ignites
        on the 'flare' variant of the ignition toggle -- a hot white core
        with jagged red rays shooting outward, fading and expanding over
        FLARE_DURATION seconds, matching the classic hand-flare VFX look.
        Confined to a local ROI around the palm like every other effect."""
        age = hs.sim_time - hs.flare_start
        fade = float(np.clip(1.0 - age / FLARE_DURATION, 0.0, 1.0))
        if fade <= 0.0:
            return
        intensity = fade ** 0.6
        # Rays travel outward as the flare fades, like a burst expanding.
        burst_radius = hs.swirl_radius * (1.3 + 1.3 * (1.0 - fade))

        roi = int(burst_radius * 3.4)
        half = roi // 2
        cx, cy = hs.palm_px
        canvas = np.zeros((roi, roi, 3), dtype=np.float32)
        local_center = np.array([half, half], dtype=np.float32)

        # Deterministic-per-ignition ray layout so the burst doesn't
        # re-randomize every frame, but does differ each time it fires.
        seed = (hash(hs.hand_key) ^ int(hs.flare_start * 1000)) % (2 ** 31)
        rng = np.random.default_rng(seed)
        n_rays = 16
        base_angles = np.linspace(0, 2 * np.pi, n_rays, endpoint=False)
        angles = base_angles + rng.uniform(-0.35, 0.35, n_rays)
        lengths = burst_radius * rng.uniform(0.7, 1.7, n_rays)
        thicknesses = rng.integers(1, 3, n_rays)

        # Small fixed-count loop (16 rays) -- cv2 line-drawing has no
        # vectorized batch API, so this is the appropriate place for a loop.
        for ang, length, thick in zip(angles, lengths, thicknesses):
            tip = local_center + length * np.array([np.cos(ang), np.sin(ang)], dtype=np.float32)
            mid = local_center + length * 0.4 * np.array([np.cos(ang), np.sin(ang)], dtype=np.float32)
            cv2.line(canvas, tuple(local_center.astype(np.int32)), tuple(tip.astype(np.int32)),
                      tuple((FLARE_RAY_COLOR * 0.9).tolist()), int(thick), cv2.LINE_AA)
            cv2.line(canvas, tuple(local_center.astype(np.int32)), tuple(mid.astype(np.int32)),
                      tuple((FLARE_HOT_COLOR * 0.8).tolist()), int(thick) + 1, cv2.LINE_AA)

        canvas = cv2.GaussianBlur(canvas, (0, 0), sigmaX=1.4)

        # Central hot core + soft red halo (the bright "sun flare" point).
        core = np.zeros_like(canvas)
        cv2.circle(core, tuple(local_center.astype(np.int32)),
                   max(3, int(burst_radius * 0.16)), tuple(FLARE_HOT_COLOR.tolist()), -1, cv2.LINE_AA)
        core = cv2.GaussianBlur(core, (0, 0), sigmaX=max(2.0, burst_radius * 0.08))

        halo = np.zeros_like(canvas)
        cv2.circle(halo, tuple(local_center.astype(np.int32)),
                   max(4, int(burst_radius * 0.32)), tuple((FLARE_RAY_COLOR * 1.1).tolist()), -1, cv2.LINE_AA)
        halo = cv2.GaussianBlur(halo, (0, 0), sigmaX=max(3.0, burst_radius * 0.22))

        canvas = (canvas + core * 1.3 + halo * 0.8) * intensity

        mask = _smoothstep_radial_mask(roi)
        canvas *= mask[..., None]

        self._composite_additive(frame, canvas, int(cx - half), int(cy - half))

    # ------------------------------------------------------------------
    def _draw_hand_link(self, frame: np.ndarray, hand_states: List[HandMagicState], t: float):
        """Draws a dense, branching lightning-bolt style energy tether
        between two active palms -- soft cyan-blue cloud glow behind a
        bright white-hot core, restruck every ~120ms so it visibly moves
        like real lightning rather than sitting as a static line. Fully
        confined to a bounding box hugging the bolt + its branches; the
        rest of the frame is never touched."""
        charged = [hs for hs in hand_states if hs.charge > LINK_ACTIVATE_THRESHOLD]
        if len(charged) < 2:
            return
        charged.sort(key=lambda h: -h.charge)
        h1, h2 = charged[0], charged[1]
        p1, p2 = h1.palm_px, h2.palm_px
        dist = float(np.linalg.norm(p2 - p1))
        if dist < 20:
            return
        link_strength = min(h1.charge, h2.charge)

        cache = self._link_cache
        endpoints_moved = (
            cache["p1"] is None
            or float(np.linalg.norm(p1 - cache["p1"])) > 12.0
            or float(np.linalg.norm(p2 - cache["p2"])) > 12.0
        )
        if t >= cache["next_strike_t"] or endpoints_moved or cache["main"] is None:
            rng = np.random.default_rng(int(t * 1000) ^ 0x5EED)
            cache["main"] = _fractal_lightning_path(p1, p2, rng, iterations=6, displace_scale=0.5)
            cache["branches"] = _spawn_lightning_branches(cache["main"], rng, n_branches=5)
            cache["p1"], cache["p2"] = p1.copy(), p2.copy()
            cache["strike_t"] = t
            cache["next_strike_t"] = t + float(rng.uniform(0.09, 0.16))

        main_pts = cache["main"]
        branches = cache["branches"]

        all_pts = np.concatenate([main_pts] + branches, axis=0) if branches else main_pts
        pad = 40
        x0 = int(all_pts[:, 0].min() - pad)
        y0 = int(all_pts[:, 1].min() - pad)
        x1 = int(all_pts[:, 0].max() + pad)
        y1 = int(all_pts[:, 1].max() + pad)
        cx0, cy0 = max(x0, 0), max(y0, 0)
        cx1, cy1 = min(x1, self.frame_w), min(y1, self.frame_h)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        w, h = cx1 - cx0, cy1 - cy0

        # Quick strobe right after each restrike (real lightning flashes
        # brighter the instant it forms, then settles), on top of the
        # overall link_strength.
        age = t - cache["strike_t"]
        strobe = 1.0 + 1.4 * np.exp(-age * 14.0)
        intensity = link_strength * strobe

        glow = np.zeros((h, w, 3), dtype=np.float32)
        core = np.zeros((h, w, 3), dtype=np.float32)

        def _draw_poly(canvas, pts, thickness, color):
            local = (pts - np.array([cx0, cy0], dtype=np.float32)).astype(np.int32)
            cv2.polylines(canvas, [local], False, tuple(float(c) for c in color), thickness, cv2.LINE_AA)

        # Outer soft cloud-glow: thick strokes, blurred hard afterward.
        _draw_poly(glow, main_pts, 7, LINK_EDGE_COLOR * 0.9)
        for b in branches:
            _draw_poly(glow, b, 4, LINK_EDGE_COLOR * 0.75)

        # Bright white-hot core: thin, crisp strokes.
        _draw_poly(core, main_pts, 2, LINK_CORE_COLOR * 1.3)
        for b in branches:
            _draw_poly(core, b, 1, LINK_CORE_COLOR * 1.1)

        glow = cv2.GaussianBlur(glow, (0, 0), sigmaX=9.0) * (0.9 * intensity)
        core = cv2.GaussianBlur(core, (0, 0), sigmaX=1.1) * (1.4 * intensity)

        canvas = glow + core
        self._composite_additive(frame, canvas, cx0, cy0)

    # ------------------------------------------------------------------
    def _composite_additive(self, frame: np.ndarray, canvas: np.ndarray, x0: int, y0: int):
        """Additive-blend a small float32 glow canvas onto the frame at
        (x0, y0), clipping to frame bounds. Additive light blending is used
        here (not alpha) because this is emissive energy, not a solid object."""
        h, w = canvas.shape[:2]
        x1, y1 = x0 + w, y0 + h
        cx0, cy0 = max(x0, 0), max(y0, 0)
        cx1, cy1 = min(x1, self.frame_w), min(y1, self.frame_h)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        sub_canvas = canvas[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
        region = frame[cy0:cy1, cx0:cx1].astype(np.float32)
        blended = np.clip(region + sub_canvas, 0, 255).astype(np.uint8)
        frame[cy0:cy1, cx0:cx1] = blended
