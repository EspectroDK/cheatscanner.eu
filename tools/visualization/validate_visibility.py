"""Validate our line-of-sight model against the game's own spotting.

CS2 demos record ``approximate_spotted_by`` (the server's coarse "enemy is on
radar because someone saw them" flag). It is not ground truth for *what the
player could see* (it lags, and ignores FOV), but it is an independent check
that our raycasting is not inventing walls or holes:

* game spotted, we say GEOMETRY_OCCLUDED  -> possible extra wall in our mesh
  (or spotting lag); we report how far (in ticks) each such case is from our
  nearest DIRECT_VISIBLE tick.
* we say visible+in-FOV, game never spotted nearby -> possible missing wall.

Usage:
    python tools/visualization/validate_visibility.py demo.dem --maps-dir data/maps --out docs/validation
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from cs2_analyzer.geometry.mesh import MapGeometry
from cs2_analyzer.geometry.smoke import SmokeModel, SmokeParams
from cs2_analyzer.geometry.visibility import LOS, VisibilityEngine
from cs2_analyzer.parser import get_parser
from cs2_analyzer.world import build_world


def nearest_gap(sorted_ticks: np.ndarray, t: int) -> int | None:
    if sorted_ticks.size == 0:
        return None
    k = np.searchsorted(sorted_ticks, t)
    return int(min(abs(int(sorted_ticks[j]) - t) for j in (k - 1, k) if 0 <= j < sorted_ticks.size))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo")
    ap.add_argument("--maps-dir", default="data/maps")
    ap.add_argument("--out", default=None, help="write summary JSON (and a case image with --render) here")
    ap.add_argument("--lag-ticks", type=int, default=32, help="tolerance for spotting lag")
    ap.add_argument("--render", action="store_true", help="render POV images of the worst disagreements")
    args = ap.parse_args(argv)

    demo = get_parser().parse(args.demo)
    w = build_world(demo)
    geo = MapGeometry.load(demo.meta.map_name, args.maps_dir)
    if not geo.available:
        raise SystemExit(f"no collision mesh for {demo.meta.map_name} in {args.maps_dir}")
    smoke = SmokeModel(demo.event("smokes"), w.tick0, w.T, w.tickrate, SmokeParams(), demo.event("he_grenades"))
    vis = VisibilityEngine(w, geo, smoke).compute()
    los = vis.los
    sb = np.transpose(w.spotted_by, (1, 0, 2))  # [observer, target, T]
    valid = los > 0
    visible = (los == LOS.DIRECT_VISIBLE) | (los == LOS.VISIBLE_THROUGH_SMOKE)
    seen = visible & vis.in_fov

    counts = {s.name: int((los == s).sum()) for s in LOS}
    game_only_occluded = []
    unmatched_segments = []
    segments = 0
    for o in range(w.P):
        for e in range(w.P):
            vt = np.nonzero(visible[o, e])[0]
            for t in np.nonzero(sb[o, e] & (los[o, e] == LOS.GEOMETRY_OCCLUDED))[0]:
                game_only_occluded.append((o, e, int(t), nearest_gap(vt, int(t))))
            s = seen[o, e].astype(np.int8)
            d = np.diff(np.r_[0, s, 0])
            for a, b in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]):
                segments += 1
                if not sb[o, e, max(0, a - args.lag_ticks):b + args.lag_ticks].any():
                    unmatched_segments.append((o, e, int(a), int(b)))

    gaps = np.array([g if g is not None else 10**6 for *_, g in game_only_occluded])
    ul = np.array([b - a for *_, a, b in unmatched_segments]) if unmatched_segments else np.array([0])
    summary = {
        "demo": Path(args.demo).name, "map": demo.meta.map_name, "pair_ticks_by_los": counts,
        "valid_pair_ticks": int(valid.sum()), "game_spotted_pair_ticks": int((sb & valid).sum()),
        "seen_and_spotted": int((seen & sb & valid).sum()),
        "game_spotted_but_geometry_occluded": len(game_only_occluded),
        "gap_ticks_to_our_visible_pct": (dict(zip(["p50", "p90", "p99", "max"],
                                                 [float(x) for x in np.percentile(gaps, [50, 90, 99, 100])]))
                                         if gaps.size else {}),
        "frac_within_lag": float((gaps <= args.lag_ticks).mean()) if gaps.size else None,
        "our_seen_segments": segments, "segments_without_game_spotting": len(unmatched_segments),
        "unmatched_segment_len_ticks_pct": dict(zip(["p50", "p90", "p99"],
                                                    [float(x) for x in np.percentile(ul, [50, 90, 99])])),
        "worst_unmatched": [
            {"observer": w.names[o], "target": w.names[e], "tick": w.tick(a), "len_ticks": b - a,
             "distance_u": float(np.linalg.norm(w.eye[o, (a + b) // 2] - w.pos[e, (a + b) // 2]))}
            for o, e, a, b in sorted(unmatched_segments, key=lambda x: x[2] - x[3])[:10]
        ],
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "visibility_validation.json").write_text(json.dumps(summary, indent=2))
        if args.render and unmatched_segments:
            _render_cases(w, geo, smoke, los, sorted(unmatched_segments, key=lambda x: x[2] - x[3])[:5],
                          out / "visibility_cases.png")
    return 0


def _render_cases(w, geo, smoke, los, cases, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from cs2_analyzer.evidence.render import project, render_pov, render_topdown
    from cs2_analyzer.geometry.angles import bearing

    fig, axes = plt.subplots(len(cases), 2, figsize=(16, 4 * len(cases)), squeeze=False)
    for (o, e, a, b), (a1, a2) in zip(cases, axes):
        t = (a + b) // 2
        eye = w.eye[o, t]
        bp = w.body_points(e, t)
        yaw, pitch = bearing(eye, bp["chest"])
        a1.imshow(render_pov(geo, eye, float(pitch), float(yaw), 480, 270, smoke=smoke, t=t))
        sx, sy, _ = project(np.array([bp[k] for k in ("head", "chest", "knee")]), eye, float(pitch), float(yaw), 480, 270)
        a1.plot(sx, sy, "m-o")
        a1.set_title(f"{w.names[o]} -> {w.names[e]} tick {w.tick(t)} LOS={LOS(int(los[o, e, t])).name}", fontsize=9)
        img, ext = render_topdown(geo, ceiling_z=max(eye[2], bp["chest"][2]) + 120)
        a2.imshow(img, extent=ext)
        a2.plot([eye[0], bp["chest"][0]], [eye[1], bp["chest"][1]], "y-")
        m = np.array([eye[:2], bp["chest"][:2]])
        c = m.mean(0)
        r = max(600, float(np.abs(m - c).max()) + 200)
        a2.set_xlim(c[0] - r, c[0] + r)
        a2.set_ylim(c[1] - r, c[1] + r)
    fig.tight_layout()
    fig.savefig(path, dpi=70)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
