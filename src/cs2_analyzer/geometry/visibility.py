"""Line-of-sight classification for every (observer, enemy, tick).

For each ordered enemy pair and live tick we cast rays from the observer's
eye to several sample points on the target body (head, chest, pelvis, knee)
through the map collision mesh and the smoke model:

``DIRECT_VISIBLE``        some body point has clear geometry and no smoke
``VISIBLE_THROUGH_SMOKE`` geometry clear, only uncertain smoke (shell, bloom,
                          fade, recently cleared) in between
``SMOKE_OCCLUDED``        geometry clear but every clear ray crosses a stable smoke core
``GEOMETRY_OCCLUDED``     every ray blocked by the map
``UNKNOWN``               no map geometry, or the pair is within the
                          uncertainty margin of an occlusion edge

Conservative margin: demo positions are server-side and clients see the world
with interpolation and peeker's advantage (the peeking client sees first).
A "blocked" result is downgraded to UNKNOWN when any of a set of perturbed
rays is clear: target points shifted sideways (shoulder width) and target /
observer positions extrapolated along their velocity by the configured
latency margin. Hidden-information detectors ignore UNKNOWN.

Derived states (RECENTLY_VISIBLE, TEAMMATE_VISIBLE, POSSIBLE_SOUND_INFORMATION)
depend on history and team context and are produced by the knowledge model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from cs2_analyzer.geometry.angles import aim_error_components, bearing
from cs2_analyzer.geometry.mesh import MapGeometry
from cs2_analyzer.geometry.smoke import SmokeModel
from cs2_analyzer.world import World


class LOS(IntEnum):
    NOT_APPLICABLE = 0
    DIRECT_VISIBLE = 1
    VISIBLE_THROUGH_SMOKE = 2
    SMOKE_OCCLUDED = 3
    GEOMETRY_OCCLUDED = 4
    UNKNOWN = 5


@dataclass
class VisibilityParams:
    end_tolerance: float = 4.0
    lateral_margin: float = 18.0
    latency_margin_ms: float = 100.0
    fov_half_yaw: float = 55.0
    fov_half_pitch: float = 42.0

    @classmethod
    def from_config(cls, cfg: dict) -> "VisibilityParams":
        return cls(**{k: float(v) for k, v in cfg.items() if k in cls.__dataclass_fields__})


class VisibilityEngine:
    def __init__(self, world: World, geometry: MapGeometry, smoke: SmokeModel, params: VisibilityParams | None = None):
        self.world = world
        self.geometry = geometry
        self.smoke = smoke
        self.params = params or VisibilityParams()
        P, T = world.P, world.T
        self.los = np.zeros((P, P, T), dtype=np.uint8)  # [observer, target, t]
        self.in_fov = np.zeros((P, P, T), dtype=bool)
        self.rays_cast = 0
        self.knowledge = None  # attached by the knowledge model

    # ------------------------------------------------------------------ compute

    def compute(self) -> "VisibilityEngine":
        w = self.world
        for o in range(w.P):
            for e in range(w.P):
                if o == e:
                    continue
                valid = w.live & w.alive[o] & w.alive[e] & w.is_enemy(o, e)
                idx = np.nonzero(valid)[0]
                if idx.size == 0:
                    continue
                self.in_fov[o, e, idx] = self._fov(o, e, idx)
                self.los[o, e, idx] = self._classify(o, e, idx)
        return self

    def _fov(self, o: int, e: int, idx: np.ndarray) -> np.ndarray:
        w = self.world
        chest = w.body_points(e, idx)["chest"]
        by, bp = bearing(w.eye[o, idx], chest)
        horiz, vert, _ = aim_error_components(w.pitch[o, idx], w.yaw[o, idx], bp, by)
        return (np.abs(horiz) <= self.params.fov_half_yaw) & (np.abs(vert) <= self.params.fov_half_pitch)

    def _rays(self, a: np.ndarray, b: np.ndarray, t: np.ndarray):
        """(geometry_clear, smoke_blocked, smoke_uncertain) for segments."""
        self.rays_cast += len(a)
        geo = self.geometry.segment_clear(a, b, self.params.end_tolerance)
        sb, su = self.smoke.occlusion(a, b, t)
        return geo, sb, su

    def _classify(self, o: int, e: int, idx: np.ndarray) -> np.ndarray:
        w = self.world
        out = np.full(idx.size, LOS.UNKNOWN, dtype=np.uint8)
        if not self.geometry.available:
            return out
        eye = w.eye[o, idx].astype(np.float64)
        pts = w.body_points(e, idx)
        any_vis = np.zeros(idx.size, bool)
        any_thr = np.zeros(idx.size, bool)
        any_smk = np.zeros(idx.size, bool)
        for name in ("head", "chest", "pelvis", "knee"):
            geo, sb, su = self._rays(eye, pts[name], idx)
            any_vis |= geo & ~sb & ~su
            any_thr |= geo & ~sb & su
            any_smk |= geo & sb
        out[:] = LOS.GEOMETRY_OCCLUDED
        out[any_smk] = LOS.SMOKE_OCCLUDED
        out[any_thr] = LOS.VISIBLE_THROUGH_SMOKE
        out[any_vis] = LOS.DIRECT_VISIBLE

        # Uncertainty margin for occluded samples.
        occ = np.nonzero((out == LOS.GEOMETRY_OCCLUDED) | (out == LOS.SMOKE_OCCLUDED))[0]
        if occ.size:
            marg_clear, marg_smoke = self._margin(o, e, idx[occ], eye[occ], {k: v[occ] for k, v in pts.items()})
            geo_occ = out[occ] == LOS.GEOMETRY_OCCLUDED
            # geometry-occluded but a perturbed ray is fully clear -> UNKNOWN
            out[occ[marg_clear]] = LOS.UNKNOWN
            # geometry-occluded but a perturbed ray is only smoke-blocked -> smoke occluded
            out[occ[geo_occ & ~marg_clear & marg_smoke]] = LOS.SMOKE_OCCLUDED
        return out

    def _margin(self, o, e, idx, eye, pts):
        w = self.world
        p = self.params
        n_lat = w.ticks_for_ms(p.latency_margin_ms)
        d = pts["chest"] - eye
        side = np.stack([-d[:, 1], d[:, 0], np.zeros(len(d))], axis=1)
        side /= np.maximum(np.linalg.norm(side, axis=1), 1e-6)[:, None]
        vel_e = np.nan_to_num(w.vel[e, idx].astype(np.float64))
        vel_o = np.nan_to_num(w.vel[o, idx].astype(np.float64))
        lead = n_lat / w.tickrate
        cand = []
        for name in ("head", "chest"):
            base = pts[name]
            cand += [(eye, base + side * p.lateral_margin), (eye, base - side * p.lateral_margin)]
            cand += [(eye, base + vel_e * lead), (eye, base - vel_e * lead)]
            cand += [(eye + vel_o * lead, base)]  # observer peeking: sees slightly ahead
        clear = np.zeros(len(idx), bool)
        smoke = np.zeros(len(idx), bool)
        for a, b in cand:
            geo, sb, su = self._rays(a, b, idx)
            clear |= geo & ~sb
            smoke |= geo & sb
        return clear, smoke

    # ------------------------------------------------------------------ queries

    def state(self, o: int, e: int, t: int) -> LOS:
        return LOS(int(self.los[o, e, t]))

    def seen(self) -> np.ndarray:
        """[o, e, T] observer had a visible line of sight AND target in FOV."""
        vis = (self.los == LOS.DIRECT_VISIBLE) | (self.los == LOS.VISIBLE_THROUGH_SMOKE)
        return vis & self.in_fov

    def check(self, observer_steam_id: int, target_steam_id: int, tick: int) -> dict:
        """Spec-level query combining LOS with the knowledge model (if attached)."""
        w = self.world
        o, e, t = w.index_of[int(observer_steam_id)], w.index_of[int(target_steam_id)], w.t(tick)
        los = LOS(int(self.los[o, e, t]))
        result = {
            "state": los.name,
            "direct_los": los in (LOS.DIRECT_VISIBLE, LOS.VISIBLE_THROUGH_SMOKE),
            "geometry_blocked": los == LOS.GEOMETRY_OCCLUDED,
            "smoke_blocked": los == LOS.SMOKE_OCCLUDED,
            "in_fov": bool(self.in_fov[o, e, t]),
            "uncertain": los in (LOS.UNKNOWN, LOS.VISIBLE_THROUGH_SMOKE),
        }
        if self.knowledge is not None:
            result.update(self.knowledge.describe(o, e, t))
        return result
