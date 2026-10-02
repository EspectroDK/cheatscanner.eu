"""Approximate smoke occlusion.

CS2 smokes are volumetric voxel fills that flow around geometry and can be
temporarily cleared by HE grenades and gunfire. Demos contain only the
detonation point and expiry tick, so the volume is modelled as two nested
ellipsoids:

* **core**: region treated as certainly opaque while the smoke is stable;
* **shell**: region where the smoke *may or may not* block sight.

Everything that cannot be modelled reliably is reported as *uncertain*
rather than opaque, so it can never create "hidden" information on its own:

* the bloom phase right after detonation and the fade phase before expiry;
* a window after an HE grenade detonates inside/near the smoke;
* a window after gunfire whose line crosses the smoke core (bullet holes).

All dimensions are UNCALIBRATED approximations (config ``geometry.smoke``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class SmokeParams:
    core_radius: float = 110.0
    core_half_height: float = 75.0
    shell_radius: float = 175.0
    shell_half_height: float = 125.0
    center_z_offset: float = 55.0
    bloom_s: float = 1.25
    fade_s: float = 2.0
    he_clear_radius: float = 260.0
    he_clear_s: float = 3.0
    gunfire_hole_s: float = 1.0
    min_core_chord: float = 24.0

    @classmethod
    def from_config(cls, cfg: dict) -> "SmokeParams":
        return cls(**{k: float(v) for k, v in cfg.items() if k in cls.__dataclass_fields__})


def _ellipsoid_chord(a: np.ndarray, b: np.ndarray, center: np.ndarray, r: float, hz: float) -> np.ndarray:
    """Length (world units) of the portion of segment a->b inside the ellipsoid."""
    scale = np.array([1.0 / r, 1.0 / r, 1.0 / hz])
    pa = (a - center) * scale
    pb = (b - center) * scale
    d = pb - pa
    A = np.sum(d * d, axis=-1)
    B = 2 * np.sum(pa * d, axis=-1)
    C = np.sum(pa * pa, axis=-1) - 1.0
    disc = B * B - 4 * A * C
    out = np.zeros(len(a))
    ok = (disc > 0) & (A > 1e-12)
    sq = np.sqrt(np.where(ok, disc, 0.0))
    with np.errstate(invalid="ignore", divide="ignore"):
        t0 = (-B - sq) / (2 * A)
        t1 = (-B + sq) / (2 * A)
    t0 = np.clip(t0, 0.0, 1.0)
    t1 = np.clip(t1, 0.0, 1.0)
    frac = np.where(ok, np.maximum(t1 - t0, 0.0), 0.0)
    out = frac * np.linalg.norm(b - a, axis=-1)
    return out


class SmokeModel:
    def __init__(self, smokes: pd.DataFrame, tick0: int, T: int, tickrate: float, params: SmokeParams,
                 he_grenades: pd.DataFrame | None = None):
        self.params = params
        self.tickrate = tickrate
        self.items: list[dict] = []
        if smokes is None or not len(smokes):
            return
        for s in smokes.itertuples():
            start = int(s.start_tick) - tick0
            end = int(s.end_tick) - tick0 if s.end_tick and s.end_tick > 0 else start + int(22 * tickrate)
            if end < 0 or start >= T:
                continue
            center = np.array([s.x, s.y, s.z + params.center_z_offset], dtype=np.float64)
            uncertain = np.zeros(max(0, end - start + 1), dtype=bool)
            nb = int(params.bloom_s * tickrate)
            nf = int(params.fade_s * tickrate)
            uncertain[:nb] = True
            if nf:
                uncertain[-nf:] = True
            if he_grenades is not None and len(he_grenades):
                for h in he_grenades.itertuples():
                    ht = int(h.tick) - tick0
                    if start <= ht <= end:
                        dist = np.linalg.norm(np.array([h.x, h.y, h.z]) - center)
                        if dist <= params.he_clear_radius:
                            a = ht - start
                            uncertain[a : a + int(params.he_clear_s * tickrate)] = True
            self.items.append({"start": start, "end": end, "center": center, "uncertain": uncertain,
                               "entity_id": int(s.entity_id)})

    def add_gunfire_holes(self, shooter_eye: np.ndarray, shooter_dir: np.ndarray, shot_t: np.ndarray, reach: float = 3000.0):
        """Mark smokes uncertain briefly after a shot line crosses their core."""
        p = self.params
        n = int(p.gunfire_hole_s * self.tickrate)
        for it in self.items:
            m = (shot_t >= it["start"]) & (shot_t <= it["end"])
            if not m.any():
                continue
            a = shooter_eye[m]
            b = a + shooter_dir[m] * reach
            chord = _ellipsoid_chord(a, b, it["center"], p.core_radius, p.core_half_height)
            for t in shot_t[m][chord > 0]:
                k = int(t) - it["start"]
                it["uncertain"][k : k + n] = True

    def occlusion(self, a: np.ndarray, b: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """For segments a->b at tick-indices t return (blocked, uncertain).

        ``blocked``: passes through a stable core for at least ``min_core_chord`` units.
        ``uncertain``: touches a shell, or a core in bloom/fade/cleared phase.
        """
        p = self.params
        blocked = np.zeros(len(a), dtype=bool)
        uncertain = np.zeros(len(a), dtype=bool)
        for it in self.items:
            m = (t >= it["start"]) & (t <= it["end"])
            if not m.any():
                continue
            idx = np.nonzero(m)[0]
            core = _ellipsoid_chord(a[idx], b[idx], it["center"], p.core_radius, p.core_half_height)
            shell = _ellipsoid_chord(a[idx], b[idx], it["center"], p.shell_radius, p.shell_half_height)
            unc_phase = it["uncertain"][np.clip(t[idx] - it["start"], 0, len(it["uncertain"]) - 1)]
            solid = (core >= p.min_core_chord) & ~unc_phase
            blocked[idx] |= solid
            uncertain[idx] |= ((shell > 0) & ~solid) | ((core > 0) & unc_phase)
        return blocked, uncertain & ~blocked

    def active_at(self, t: int) -> list[dict]:
        return [it for it in self.items if it["start"] <= t <= it["end"]]
