"""Find mesh triangles that block sight lines the game itself reports as visible.

A collision mesh can contain surfaces a player can see through in the match,
for example door openings whose closed-door collision is baked into
world_physics, or thin props. Those are the dangerous error for this analyzer:
an enemy that is really visible is classified GEOMETRY_OCCLUDED, and a player
tracking it looks like they use hidden information.

For each demo we take the pair-ticks where the game's ``approximate_spotted_by``
says the observer saw the target while our model says GEOMETRY_OCCLUDED and
our nearest visible tick for that pair is more than ``--lag-ticks`` away
(spotting lag cannot explain it). Contiguous runs of such ticks are
"episodes". Every triangle crossed by the four body-point rays of an episode is
counted once per episode. Triangles that block at least ``--min-episodes``
episodes, in at least ``--min-demos`` demos, are reported and, with
``--write``, removed from the mesh (the removed triangles are kept in
``<map>.removed.tri``). Removing geometry only makes the visibility model more
permissive, the safe direction.

    python tools/geometry/audit_mesh.py data/maps/de_nuke.tri demo1.dem demo2.dem ... [--write]
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

import numpy as np

from cs2_analyzer.geometry.mesh import MapGeometry, drop_degenerate, load_tri
from cs2_analyzer.geometry.smoke import SmokeModel, SmokeParams
from cs2_analyzer.geometry.visibility import LOS, VisibilityEngine
from cs2_analyzer.parser import get_parser
from cs2_analyzer.world import build_world

BODY = ("head", "chest", "pelvis", "knee")


def crossed(scene, a: np.ndarray, b: np.ndarray, end_tol: float = 4.0, max_hits: int = 16) -> list[set[int]]:
    """Triangle ids crossed by each segment a->b (all of them, not just the first)."""
    d = b - a
    length = np.linalg.norm(d, axis=1)
    u = d / np.maximum(length, 1e-6)[:, None]
    pos = end_tol * np.ones(len(a))
    out = [set() for _ in range(len(a))]
    active = np.arange(len(a))
    for _ in range(max_hits):
        if not active.size:
            break
        o = (a[active] + u[active] * pos[active][:, None]).astype(np.float32)
        md = np.maximum(length[active] - end_tol - pos[active], 1e-3).astype(np.float32)
        res = scene.run(o, u[active].astype(np.float32), dists=md, output=1)
        prim = np.asarray(res["primID"])
        tfar = np.asarray(res["tfar"], dtype=np.float64)
        hit = prim >= 0
        for i, p in zip(active[hit], prim[hit]):
            out[i].add(int(p))
        pos[active[hit]] += tfar[hit] + 0.5
        active = active[hit]
    return out


def episodes(ticks: np.ndarray, gap: int = 8) -> list[np.ndarray]:
    if not ticks.size:
        return []
    cuts = np.nonzero(np.diff(ticks) > gap)[0] + 1
    return np.split(ticks, cuts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tri")
    ap.add_argument("demos", nargs="+")
    ap.add_argument("--lag-ticks", type=int, default=32)
    ap.add_argument("--min-episode-ticks", type=int, default=8)
    ap.add_argument("--min-episodes", type=int, default=2)
    ap.add_argument("--min-demos", type=int, default=1)
    ap.add_argument("--patch-tolerance", type=int, default=10,
                    help="use only demos within this many patches of the mesh (<map>.tri.json); maps change "
                         "between patches, and old demos would remove walls that exist today")
    ap.add_argument("--write", action="store_true", help="remove the flagged triangles from the mesh")
    args = ap.parse_args(argv)
    import json

    meta_path = Path(args.tri + ".json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    mesh_patch = meta.get("patch_version")

    from embreex import rtcore_scene as rtcs
    from embreex.mesh_construction import TriangleMesh

    tri_path = Path(args.tri)
    tris = drop_degenerate(load_tri(tri_path))
    map_name = tri_path.stem
    geo = MapGeometry(map_name, tris, source=str(tri_path))
    scene = rtcs.EmbreeScene()
    TriangleMesh(scene, np.ascontiguousarray(tris, dtype=np.float32))

    ep_count = collections.Counter()
    demo_sets: dict[int, set[str]] = collections.defaultdict(set)
    total_eps = 0
    for dem in args.demos:
        demo = get_parser().parse(dem)
        if demo.meta.map_name != map_name:
            print(f"skip {Path(dem).name}: map {demo.meta.map_name}")
            continue
        dp = int(demo.meta.patch_version) if str(demo.meta.patch_version or "").isdigit() else None
        if mesh_patch and (dp is None or abs(dp - mesh_patch) > args.patch_tolerance):
            print(f"skip {Path(dem).name}: patch {dp}, mesh {mesh_patch}")
            continue
        w = build_world(demo)
        smoke = SmokeModel(demo.event("smokes"), w.tick0, w.T, w.tickrate, SmokeParams(), demo.event("he_grenades"))
        vis = VisibilityEngine(w, geo, smoke).compute()
        los = vis.los
        n_eps = 0
        for o in range(w.P):
            for e in range(w.P):
                if o == e:
                    continue
                vt = np.nonzero((los[o, e] == LOS.DIRECT_VISIBLE) | (los[o, e] == LOS.VISIBLE_THROUGH_SMOKE))[0]
                bad = np.nonzero(w.spotted_by[e, o] & (los[o, e] == LOS.GEOMETRY_OCCLUDED))[0]
                if vt.size:
                    near = np.abs(vt[np.clip(np.searchsorted(vt, bad), 0, vt.size - 1)] - bad)
                    near = np.minimum(near, np.abs(vt[np.clip(np.searchsorted(vt, bad) - 1, 0, vt.size - 1)] - bad))
                    bad = bad[near > args.lag_ticks]
                for ep in episodes(bad):
                    if ep.size < args.min_episode_ticks:
                        continue
                    ts = ep[:: max(1, ep.size // 16)]
                    pts = w.body_points(e, ts)
                    eye = w.eye[o, ts].astype(np.float64)
                    hit: set[int] = set()
                    for k in BODY:
                        for s in crossed(scene, eye, np.asarray(pts[k], dtype=np.float64)):
                            hit |= s
                    for p in hit:
                        ep_count[p] += 1
                        demo_sets[p].add(Path(dem).name)
                    n_eps += 1
        total_eps += n_eps
        print(f"{Path(dem).name}: {n_eps} persistent spotted-but-occluded episodes")

    flagged = sorted(p for p, c in ep_count.items() if c >= args.min_episodes and len(demo_sets[p]) >= args.min_demos)
    print(f"{map_name}: {total_eps} episodes, {len(ep_count)} triangles involved, {len(flagged)} flagged")
    if flagged:
        cen = tris[flagged].reshape(-1, 3, 3).mean(axis=1)
        cells = collections.Counter(tuple((np.round(c / 128) * 128).astype(int)) for c in cen)
        for cell, n in cells.most_common(10):
            print(f"  around {list(cell)}: {n} triangles")
    if args.write and flagged:
        keep = np.ones(len(tris), dtype=bool)
        keep[flagged] = False
        tris[~keep].astype("<f4").tofile(tri_path.with_suffix(".removed.tri"))
        tris[keep].astype("<f4").tofile(tri_path)
        if meta:
            meta["removed_by_audit"] = int(meta.get("removed_by_audit", 0)) + len(flagged)
            meta["triangles"] = int(keep.sum())
            meta_path.write_text(json.dumps(meta, indent=2))
        print(f"wrote {tri_path} ({keep.sum()} triangles) and {tri_path.with_suffix('.removed.tri')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
