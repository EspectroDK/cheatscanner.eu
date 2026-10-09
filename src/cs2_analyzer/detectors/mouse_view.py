"""Mouse fields of the user commands: the absolute view angle where mouse movement belongs.

**Why.** Every user command carries the mouse counts of that tick
(``usercmd_mouse_dx``/``dy``), and with a mouse the view turns by those counts
times sensitivity × 0.022°. Over a match the recorded counts follow the view
turn closely (correlation about 0.94 for clean CS2CD players, 0.92 on Valve
matchmaking demos; the demo keeps one command per tick, so single ticks do not
match exactly). For some players the mouse fields hold something else: the
view angle itself, ``dx = -yaw / 0.022`` and ``dy = pitch / 0.022`` to within
a count, tick after tick. A mouse cannot produce that. Where it comes from is
not known; on CS2CD it is almost only found in cheater matches.

**What is measured.** Per player, over live, alive, unscoped ticks with mouse
data: ``absolute_share``, the share of ticks in that state, plus ``agreement``
(|correlation| of the yaw change with ``dx`` on the other ticks), the gain and
the share of view movement the counts do not explain, for reference. A weak
``agreement`` on its own is no evidence (and on CS2CD, once absolute-angle ticks
are set aside, none is left below 0.2).

**False-positive controls.**

* Evidence needs ``absolute_share`` of at least ``min_absolute_share`` (0.8). On
  all 795 CS2CD matches no clean player reaches it; the highest clean share is
  0.45 (one player whose fields switch to angles mid-match),
  docs/validation/cs2cd/mouse_view.md.
* At least ``min_mouse_ticks`` qualifying ticks and mouse fields on at least
  ``min_mouse_share`` of them; demos without user commands produce nothing.
* Ticks with counts below 3 on both axes never match (a still player looking
  at yaw 0). Scoped, dead, frozen and teleport ticks are excluded.
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp


M_YAW = 0.022  # degrees per mouse count at sensitivity 1


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def absolute_angle_ticks(yaw: np.ndarray, pitch: np.ndarray, dx: np.ndarray, dy: np.ndarray, tol: float = 1.5) -> np.ndarray:
    """Ticks whose "mouse counts" are the view angle itself: dx = -yaw / 0.022, dy = pitch / 0.022.

    Ticks with tiny counts are left out, so a player looking at yaw 0 without moving does not match.
    """
    return ((np.abs(dx + yaw / M_YAW) <= tol) & (np.abs(dy - pitch / M_YAW) <= tol)
            & ((np.abs(dx) >= 3) | (np.abs(dy) >= 3)))


def mouse_view_stats(yaw: np.ndarray, pitch: np.ndarray, dx: np.ndarray, dy: np.ndarray, ok: np.ndarray) -> dict:
    """Mouse fields of the recorded user commands against the view, on the ``ok`` ticks.

    ``absolute_share``: share of ticks whose mouse fields hold the absolute view angle
    (:func:`absolute_angle_ticks`); ``agreement``: |corr(yaw change, dx)| over the other ticks where
    the view moved; ``gain_yaw``: degrees per count; ``unexplained``: sum |view change - gain * counts|
    / sum |view change|; ``no_mouse_share``: share of the view movement on ticks without any count.
    """
    dyaw = np.r_[np.nan, _wrap(np.diff(yaw))]
    dpitch = np.r_[np.nan, np.diff(pitch)]
    has = ok & np.isfinite(dx) & np.isfinite(dy) & np.isfinite(dyaw) & np.isfinite(dpitch)
    absolute = has & absolute_angle_ticks(yaw, pitch, dx, dy)
    n = max(int(has.sum()), 1)
    mv = np.hypot(dyaw, dpitch)
    rel = has & ~absolute
    moved = rel & (mv > 1e-4)
    out = {"ticks": int(ok.sum()), "mouse_ticks": int(has.sum()), "moved_ticks": int(moved.sum()),
           "absolute_ticks": int(absolute.sum()), "absolute_share": float(absolute.sum() / n),
           "agreement": float("nan"), "gain_yaw": float("nan"), "unexplained": float("nan"), "no_mouse_share": float("nan")}
    if moved.sum() < 30:
        return out
    x, y = dx[moved], dyaw[moved]
    if x.std() > 0 and y.std() > 0:
        out["agreement"] = float(abs(np.corrcoef(y, x)[0, 1]))
    m = moved & (dx != 0) & (np.abs(dyaw) > 1e-5)
    if m.sum() >= 30:
        ky = float(np.median(-dyaw[m] / dx[m]))
        mp = moved & (dy != 0) & (np.abs(dpitch) > 1e-5)
        kp = float(np.median(dpitch[mp] / dy[mp])) if mp.sum() >= 30 else ky
        resid = np.hypot(dyaw + ky * dx, dpitch - kp * dy)
        out["gain_yaw"] = ky
        out["unexplained"] = float(resid[moved].sum() / mv[moved].sum())
    out["no_mouse_share"] = float(mv[moved & (dx == 0) & (dy == 0)].sum() / mv[moved].sum())
    return out


class MouseViewDetector(Detector):
    name = "mouse_view"
    axis = EvidenceAxis.IMPOSSIBLE_MECHANICS
    group = EvidenceGroup.IMPOSSIBLE
    default_reliability = 0.6

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        if w.mouse_dx is None or w.mouse_dy is None:
            return []
        max_step = float(cfg.get("max_step_deg", 40.0))
        min_share = float(cfg.get("min_absolute_share", 0.8))
        full_share = float(cfg.get("full_absolute_share", 0.95))
        min_ticks = int(cfg.get("min_mouse_ticks", 2000))
        min_mouse_share = float(cfg.get("min_mouse_share", 0.9))
        events = []
        for p in range(w.P):
            if not ctx.analyze_player(p):
                continue
            dyaw = np.r_[np.nan, _wrap(np.diff(w.yaw[p]))]
            dpitch = np.r_[np.nan, np.diff(w.pitch[p])]
            ok = (w.alive[p] & np.r_[False, w.alive[p][:-1]] & w.live & np.r_[False, w.live[:-1]]
                  & ~w.scoped[p] & np.r_[False, ~w.scoped[p][:-1]]
                  & np.isfinite(dyaw) & np.isfinite(dpitch)
                  & (np.abs(dyaw) < max_step) & (np.abs(dpitch) < max_step))
            s = mouse_view_stats(w.yaw[p], w.pitch[p], w.mouse_dx[p].astype(np.float64),
                                 w.mouse_dy[p].astype(np.float64), ok)
            if not s["mouse_ticks"]:
                continue
            s["mouse_share"] = s["mouse_ticks"] / max(s["ticks"], 1)
            s["sensitivity"] = s["gain_yaw"] / M_YAW if np.isfinite(s["gain_yaw"]) else float("nan")
            ctx.observe(f"{self.name}_summary", p, **s)
            share = s["absolute_share"]
            if not (s["mouse_ticks"] >= min_ticks and s["mouse_share"] >= min_mouse_share and share >= min_share):
                continue
            sev = 0.4 + 0.6 * ramp(share, min_share, full_share)
            idx = np.nonzero(ok)[0]
            metrics = {k: s[k] for k in ("absolute_share", "absolute_ticks", "mouse_ticks", "agreement")}
            events.append(self.event(
                ctx, p, int(idx[0]), int(idx[len(idx) // 2]), int(idx[-1]), sev, 1.0, None, metrics, {"scope": "match"},
                f"On {share * 100:.0f}% of {s['mouse_ticks']} ticks the mouse fields of the player's commands held the "
                f"view angle itself (yaw and pitch / 0.022) instead of mouse movement. A mouse does not produce "
                f"this; where it comes from is not known, and one clean CS2CD player shows it for part of a match."))
        return events
