"""Map collision geometry and ray/segment occlusion queries.

Map geometry is a triangle soup extracted from the map's physics collision
mesh (``.vphys``). Two on-disk ``.tri`` encodings are accepted:

* raw little-endian float32, 9 floats per triangle (awpy / cs2-map-parser binary)
* the same bytes written as space-separated hex text (the file published in
  AtomicBool/cs2-map-parser ``vischeck_example/mirage.tri``)

Raycasting uses Intel Embree (``embreex``) when available. A brute-force numpy
implementation exists for tests and tiny synthetic maps.

Known limitations (see docs/geometry.md): the collision mesh is not the render
mesh. Thin see-through surfaces (fences, grates) and dynamic props (doors)
may be wrong or missing. Degenerate triangles in the source mesh are dropped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

NO_HIT = np.inf


def load_tri(path: str | Path) -> np.ndarray:
    """Load a ``.tri`` file into an ``(N, 3, 3)`` float32 array."""
    data = Path(path).read_bytes()
    head = data[:64]
    if head and all(c in b"0123456789abcdefABCDEF \n\r\t" for c in head):
        data = bytes.fromhex(data.decode("ascii").replace("\n", " ").replace("\r", " "))
    arr = np.frombuffer(data[: len(data) - len(data) % 36], dtype="<f4")
    tris = arr.reshape(-1, 3, 3).copy()
    return drop_degenerate(tris)


def drop_degenerate(tris: np.ndarray, min_area: float = 1e-3) -> np.ndarray:
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    area = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)
    finite = np.isfinite(tris).all(axis=(1, 2))
    return tris[(area > min_area) & finite]


def box_triangles(mins, maxs) -> np.ndarray:
    """12 triangles of an axis-aligned box (for synthetic test maps)."""
    x0, y0, z0 = mins
    x1, y1, z1 = maxs
    v = np.array(
        [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]],
        dtype=np.float32,
    )
    faces = [
        (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
        (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0),
    ]
    return np.array([[v[a], v[b], v[c]] for a, b, c in faces], dtype=np.float32)


class Raycaster(Protocol):
    def hit_distance(self, origins: np.ndarray, directions: np.ndarray, max_dist: np.ndarray) -> np.ndarray:
        """Distance to first hit along each unit direction, ``inf`` if none within max_dist."""


class NumpyRaycaster:
    """Brute-force Möller–Trumbore. O(rays x triangles): tests / tiny maps only."""

    def __init__(self, triangles: np.ndarray):
        self.tris = np.asarray(triangles, dtype=np.float64)

    def hit_distance(self, origins, directions, max_dist):
        o = np.asarray(origins, dtype=np.float64)
        d = np.asarray(directions, dtype=np.float64)
        best = np.full(len(o), NO_HIT)
        if len(self.tris) == 0 or len(o) == 0:
            return best
        v0, v1, v2 = self.tris[:, 0], self.tris[:, 1], self.tris[:, 2]
        e1, e2 = v1 - v0, v2 - v0
        chunk = max(1, 200000 // max(1, len(self.tris)))
        for s in range(0, len(o), chunk):
            oo = o[s : s + chunk, None, :]
            dd = d[s : s + chunk, None, :]
            pvec = np.cross(dd, e2[None])
            det = np.sum(e1[None] * pvec, axis=-1)
            ok = np.abs(det) > 1e-9
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tvec = oo - v0[None]
            u = np.sum(tvec * pvec, axis=-1) * inv
            qvec = np.cross(tvec, e1[None])
            v = np.sum(dd * qvec, axis=-1) * inv
            t = np.sum(e2[None] * qvec, axis=-1) * inv
            hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6)
            t = np.where(hit, t, NO_HIT)
            best[s : s + chunk] = t.min(axis=1)
        md = np.asarray(max_dist, dtype=np.float64)
        best[best > md] = NO_HIT
        return best


    def cast(self, origins, directions):
        dist = self.hit_distance(origins, directions, np.full(len(origins), np.inf))
        n = np.zeros((len(origins), 3))
        n[:, 2] = 1.0  # shading only; the brute-force backend does not track normals
        return dist, n


class EmbreeRaycaster:
    def __init__(self, triangles: np.ndarray):
        from embreex import rtcore_scene as rtcs
        from embreex.mesh_construction import TriangleMesh

        self._scene = rtcs.EmbreeScene()
        self._mesh = TriangleMesh(self._scene, np.ascontiguousarray(triangles, dtype=np.float32))

    def hit_distance(self, origins, directions, max_dist):
        o = np.ascontiguousarray(origins, dtype=np.float32)
        d = np.ascontiguousarray(directions, dtype=np.float32)
        out = np.full(len(o), NO_HIT)
        if len(o) == 0:
            return out
        md = np.ascontiguousarray(max_dist, dtype=np.float32)
        res = self._scene.run(o, d, dists=md, output=1)
        tfar = np.asarray(res["tfar"], dtype=np.float64)
        prim = np.asarray(res["primID"])
        hit = prim >= 0
        out[hit] = tfar[hit]
        return out

    def cast(self, origins, directions):
        """(distance, unit normal) of first hit; distance inf where nothing is hit."""
        o = np.ascontiguousarray(origins, dtype=np.float32)
        d = np.ascontiguousarray(directions, dtype=np.float32)
        res = self._scene.run(o, d, output=1)
        prim = np.asarray(res["primID"])
        dist = np.where(prim >= 0, np.asarray(res["tfar"], dtype=np.float64), NO_HIT)
        n = np.asarray(res["Ng"], dtype=np.float64)
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-9)[:, None]
        return dist, n


def make_raycaster(triangles: np.ndarray, backend: str = "auto") -> Raycaster:
    if backend in ("auto", "embree"):
        try:
            return EmbreeRaycaster(triangles)
        except ImportError:
            if backend == "embree":
                raise
    return NumpyRaycaster(triangles)


class MapGeometry:
    """Triangle mesh + raycaster for one map, or an explicit 'unavailable' marker."""

    def __init__(self, map_name: str, triangles: np.ndarray | None, source: str | None = None, backend: str = "auto"):
        self.map_name = map_name
        self.source = source
        self.triangles = triangles
        self.raycaster: Raycaster | None = make_raycaster(triangles, backend) if triangles is not None else None
        self.patch_version: int | None = None  # game patch the mesh was built from (<map>.tri.json), if known

    @property
    def available(self) -> bool:
        return self.raycaster is not None

    @classmethod
    def load(cls, map_name: str, maps_dir: str | Path, backend: str = "auto") -> "MapGeometry":
        maps_dir = Path(maps_dir)
        for cand in (maps_dir / f"{map_name}.tri", maps_dir / f"{map_name.removeprefix('de_')}.tri"):
            if cand.exists():
                geo = cls(map_name, load_tri(cand), source=str(cand), backend=backend)
                meta = cand.with_name(cand.name + ".json")
                if meta.exists():
                    import json

                    try:
                        geo.patch_version = int(json.loads(meta.read_text())["patch_version"])
                    except (ValueError, KeyError, TypeError):
                        pass
                return geo
        return cls(map_name, None, source=None)

    def segment_clear(self, a: np.ndarray, b: np.ndarray, end_tolerance: float = 4.0) -> np.ndarray:
        """True where the segment a->b is not blocked by geometry.

        Hits within ``end_tolerance`` units of either endpoint are ignored: the
        target point may touch the floor/wall it stands against, and demo
        positions carry interpolation error of a few units.
        """
        a = np.asarray(a, dtype=np.float64)
        b = np.asarray(b, dtype=np.float64)
        d = b - a
        length = np.linalg.norm(d, axis=-1)
        safe = np.maximum(length, 1e-6)
        u = d / safe[:, None]
        start = a + u * np.minimum(end_tolerance, safe / 2)[:, None]
        max_dist = np.maximum(length - 2 * end_tolerance, 0.0)
        if self.raycaster is None:
            raise RuntimeError("map geometry unavailable")
        hit = self.raycaster.hit_distance(start, u, np.maximum(max_dist, 1e-3))
        return ~np.isfinite(hit) | (hit >= max_dist) | (max_dist <= 0)
