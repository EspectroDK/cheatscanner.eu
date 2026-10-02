"""View-angle integrity around shots: snap-and-return and pinned pitch.

**Snap-and-return.** Silent aim and "rage" aimbots set the view (the angles
the server uses for the shot) onto the target for the firing tick only, and
the view is back where it was on the next tick. A human flick takes several
ticks and does not return along its own path within one tick. For every gun
shot at tick ``t`` we compare the view at ``t-1``, ``t`` and ``t+1``:

* ``jump``   angle between the views at t-1 and t
* ``return`` angle between the views at t and t+1
* ``net``    angle between the views at t-1 and t+1

A shot is a snap-and-return when the jump is at least ``min_jump_deg``, the
view comes back at least ``min_return_ratio`` of the way, the net movement is
less than ``max_net_ratio`` of the jump and the jump is ``min_neighbour_ratio``
times larger than the movement on the ticks around it (so fast continuous
flicks do not qualify).

**Pinned pitch.** Anti-aim ("spin-bot") cheats hold the pitch at the limit
(looking straight down) while moving; the shot tick then snaps to the target.
We record the fraction of moving, alive, live-round ticks with |pitch| at the
limit.

On the 121 CS2CD Mirage matches no clean player without pinned pitch produced
a snap-and-return shot; the only "clean" players with them held their pitch
pinned for more than half the match, i.e. unbanned rage cheaters
(docs/validation/cs2cd). One snap can still be a demo artefact, so evidence
needs ``min_snaps`` of them in a match.
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.geometry.angles import angle_between_vectors, view_vector


class ViewIntegrityDetector(Detector):
    name = "view_integrity"
    axis = EvidenceAxis.IMPOSSIBLE_MECHANICS
    group = EvidenceGroup.IMPOSSIBLE
    default_reliability = 0.8

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        min_jump = float(cfg.get("min_jump_deg", 2.0))
        min_return = float(cfg.get("min_return_ratio", 0.7))
        max_net = float(cfg.get("max_net_ratio", 0.5))
        min_neigh = float(cfg.get("min_neighbour_ratio", 5.0))
        min_snaps = int(cfg.get("min_snaps", 2))
        full_snaps = int(cfg.get("full_snaps", 8))
        pitch_limit = float(cfg.get("pitch_limit_deg", 88.9))
        min_pinned = float(cfg.get("min_pinned_fraction", 0.1))
        full_pinned = float(cfg.get("full_pinned_fraction", 0.4))
        min_moving = int(cfg.get("min_moving_ticks", 640))
        events = []
        for p in range(w.P):
            if not ctx.analyze_player(p):
                continue
            v = view_vector(w.pitch[p], w.yaw[p])
            st = w.shot_ticks.get(p, np.array([], dtype=int))
            st = st[(st >= 2) & (st < w.T - 2)]
            st = st[w.alive[p, st] & w.alive[p, st + 1]]
            snaps = []
            if st.size:
                a = lambda i, j: angle_between_vectors(v[st + i], v[st + j])  # noqa: E731
                jump, back, net = a(-1, 0), a(0, 1), a(-1, 1)
                neigh = np.nanmax(np.stack([a(-2, -1), a(1, 2)]), axis=0)
                is_snap = ((jump >= min_jump) & (back >= min_return * jump) & (net <= max_net * jump)
                           & (jump >= min_neigh * np.maximum(neigh, 0.05)))
                for k, t in enumerate(st):
                    ctx.observe(self.name, p, tick=w.tick(int(t)), round=int(w.round_of[t]), weapon=w.weapon[p, t],
                                jump_deg=float(jump[k]), return_deg=float(back[k]), net_deg=float(net[k]),
                                neighbour_deg=float(neigh[k]), snap_return=bool(is_snap[k]))
                snaps = [(int(t), float(jump[k])) for k, t in enumerate(st) if is_snap[k]]

            moving = w.alive[p] & w.live & (w.speed2d[p] > 50)
            n_moving = int(moving.sum())
            pinned = float((np.abs(w.pitch[p][moving]) >= pitch_limit).mean()) if n_moving else float("nan")
            ctx.observe(f"{self.name}_summary", p, shots=int(st.size), snap_returns=len(snaps),
                        moving_ticks=n_moving, pinned_pitch_fraction=pinned)

            if len(snaps) >= min_snaps:
                ticks = [t for t, _ in snaps]
                sev = 0.4 + 0.6 * ramp(len(snaps), min_snaps, full_snaps)
                metrics = {"snap_returns": len(snaps), "shots": int(st.size),
                           "median_jump_deg": float(np.median([j for _, j in snaps])),
                           "snap_ticks": [w.tick(t) for t in ticks][:20]}
                events.append(self.event(
                    ctx, p, min(ticks), ticks[len(ticks) // 2], max(ticks), sev, 1.0, None, metrics, {"scope": "match"},
                    f"On {len(snaps)} of {st.size} shots the view jumped {metrics['median_jump_deg']:.1f} deg (median) "
                    f"on the firing tick and returned on the next tick, a pattern of silent aim."))
            if n_moving >= min_moving and pinned >= min_pinned:
                sev = 0.4 + 0.6 * ramp(pinned, min_pinned, full_pinned)
                t_mid = int(np.nonzero(moving)[0][n_moving // 2])
                events.append(self.event(
                    ctx, p, int(np.nonzero(moving)[0][0]), t_mid, int(np.nonzero(moving)[0][-1]), sev, 1.0, None,
                    {"pinned_pitch_fraction": pinned, "moving_ticks": n_moving}, {"scope": "match"},
                    f"Pitch held at the {pitch_limit:.0f} deg limit on {pinned * 100:.0f}% of moving ticks, "
                    f"a pattern of anti-aim cheats."))
        return events
