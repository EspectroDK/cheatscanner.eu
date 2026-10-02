"""Which map meshes are installed, and whether they still match the game patch of recent demos.

Used by ``cs2-analyzer maps-check`` (the server-side check behind ``tools/deploy/push-maps.ps1``) and
by the admin page, which flags a map whose mesh is older than the demos being analyzed on it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from cs2_analyzer.geometry.mesh import load_tri

MIN_TRIANGLES = 1000   # a real CS2 map has hundreds of thousands; fewer means a broken build


def mesh_patch(path: Path) -> int | None:
    """Game patch the mesh was built from, from ``<map>.tri.json`` next to it."""
    meta = path.with_name(path.name + ".json")
    try:
        return int(json.loads(meta.read_text())["patch_version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def check_mesh(path: str | Path) -> tuple[int, str | None]:
    """(triangle count, problem or None) for one ``.tri`` file."""
    path = Path(path)
    try:
        tris = load_tri(path)
    except (OSError, ValueError) as e:
        return 0, f"unreadable: {e}"
    if len(tris) < MIN_TRIANGLES:
        return len(tris), f"only {len(tris)} triangles"
    extent = float(np.abs(tris).max())
    if extent > 1e6:
        return len(tris), f"coordinates out of range ({extent:.0f})"
    meta = path.with_name(path.name + ".json")
    if meta.exists() and mesh_patch(path) is None:
        return len(tris), f"{meta.name} has no readable patch_version"
    return len(tris), None


def _map_name(path: Path) -> str:
    name = path.name.removesuffix(".tri")
    return name if "_" in name else f"de_{name}"


def map_status(maps_dir: str | Path, render_dir: str | Path | None, demo_patches: dict[str, int],
               tolerance: int) -> list[dict]:
    """One row per map that has a mesh or recent demos.

    ``demo_patches`` is the newest game patch seen per map in recent demos. A map is ``stale`` when that
    patch is more than ``tolerance`` ahead of the mesh's, and ``missing`` when demos arrive with no mesh.
    """
    maps_dir = Path(maps_dir)
    render = Path(render_dir) if render_dir else None
    meshes = {_map_name(p): p for p in sorted(maps_dir.glob("*.tri"))} if maps_dir.is_dir() else {}
    rows = []
    for name in sorted(set(meshes) | set(demo_patches)):
        p = meshes.get(name)
        patch = mesh_patch(p) if p else None
        demo = demo_patches.get(name)
        has_render = bool(render and any((render / f).exists() for f in (f"{name}.tri", f"{name.removeprefix('de_')}.tri")))
        rows.append({
            "map": name,
            "mesh": p is not None,
            "renderMesh": has_render,
            "meshPatch": patch,
            "meshUpdatedAt": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat() if p else None,
            "latestDemoPatch": demo,
            "missing": p is None,
            "stale": bool(p is not None and patch and demo and demo - patch > tolerance),
        })
    return rows
