"""Software rendering from the map collision mesh (no CS2 client needed).

* :func:`render_pov` ray-casts a shaded first-person image from a player's eye
  with their exact view angles. It is used to validate visibility decisions and
  as the "suspect POV" panel of evidence clips.
* :func:`render_topdown` ray-casts downward from a chosen ceiling height to build
  a hill-shaded radar-like map image.

The collision mesh has no textures and may miss thin see-through props, so
these images are geometry views, not screenshots.
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.geometry.angles import RAD, view_vector
from cs2_analyzer.geometry.mesh import MapGeometry
from cs2_analyzer.geometry.smoke import SmokeModel, _ellipsoid_chord

# CS2 default: 90 deg horizontal FOV defined at 4:3, i.e. ~106.26 deg at 16:9.
HFOV_16_9 = 106.26


def camera_basis(pitch: float, yaw: float):
    fwd = view_vector(pitch, yaw)
    right = np.array([np.sin(yaw * RAD), -np.cos(yaw * RAD), 0.0])
    up = np.cross(right, fwd)
    return fwd, right, up


SUN = np.array([0.45, 0.25, 0.86])
SUN /= np.linalg.norm(SUN)
SKY_TOP = np.array([0.42, 0.62, 0.86])
SKY_HORIZON = np.array([0.78, 0.84, 0.90])
FLOOR = np.array([0.80, 0.72, 0.56])
WALL = np.array([0.86, 0.78, 0.63])
EDGE = np.array([0.18, 0.15, 0.12])
PLAYER_COLORS = {2: np.array([0.93, 0.66, 0.20]), 3: np.array([0.30, 0.58, 0.92])}


def _capsule_hits(origins, dirs, a, b, radius):
    """Distance along each ray to a capsule a-b (inf where missed). Rays are unit length."""
    ba = b - a
    oa = origins - a
    baba = ba @ ba
    bard = dirs @ ba
    baoa = oa @ ba
    rdoa = np.einsum("ij,ij->i", dirs, oa)
    oaoa = np.einsum("ij,ij->i", oa, oa)
    qa = baba - bard * bard
    qb = baba * rdoa - baoa * bard
    qc = baba * oaoa - baoa * baoa - radius * radius * baba
    h = qb * qb - qa * qc
    dist = np.full(len(origins), np.inf)
    ok = (h >= 0) & (qa > 1e-9)
    with np.errstate(invalid="ignore", divide="ignore"):
        t = (-qb - np.sqrt(np.maximum(h, 0))) / qa
        y = baoa + t * bard
        body = ok & (y > 0) & (y < baba) & (t > 0)
        dist[body] = t[body]
        # end caps
        for c in (a, b):
            oc = origins - c
            bb = np.einsum("ij,ij->i", dirs, oc)
            cc = np.einsum("ij,ij->i", oc, oc) - radius * radius
            hh = bb * bb - cc
            tc = -bb - np.sqrt(np.maximum(hh, 0))
            cap = (hh >= 0) & (tc > 0) & (tc < dist)
            dist[cap] = tc[cap]
    return dist


def render_pov(geometry: MapGeometry, eye, pitch: float, yaw: float, width: int = 320, height: int = 180,
               hfov: float = HFOV_16_9, smoke: SmokeModel | None = None, t: int | None = None,
               players: list[dict] | None = None, shadows: bool = True, edges: bool = True) -> np.ndarray:
    """Return an ``(H, W, 3)`` float image in [0, 1].

    ``players`` are figures to draw as simple capsules, each ``{"feet", "eye", "team"}``. They are
    depth-tested against the map, so a figure only shows where it was actually in view.
    """
    eye = np.asarray(eye, dtype=np.float64)
    fwd, right, up = camera_basis(pitch, yaw)
    tan_h = np.tan(hfov / 2 * RAD)
    tan_v = tan_h * height / width
    u = (np.arange(width) + 0.5) / width * 2 - 1
    v = 1 - (np.arange(height) + 0.5) / height * 2
    uu, vv = np.meshgrid(u, v)
    dirs = fwd[None, None] + uu[..., None] * tan_h * right + vv[..., None] * tan_v * up
    dirs = dirs.reshape(-1, 3)
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    origins = np.repeat(eye[None], len(dirs), axis=0)
    sky = SKY_HORIZON + (SKY_TOP - SKY_HORIZON) * np.clip(dirs[:, 2:3] * 2.5, 0, 1)
    if geometry.raycaster is None:
        return np.full((height, width, 3), 0.3)
    dist, normal = geometry.raycaster.cast(origins, dirs)
    hit = np.isfinite(dist)
    d = np.where(hit, dist, 0.0)
    pts = origins + dirs * d[:, None]
    # face the camera (collision meshes have arbitrary winding)
    flip = np.einsum("ij,ij->i", normal, dirs) > 0
    normal = np.where(flip[:, None], -normal, normal)
    floor = normal[:, 2] > 0.7
    base = np.where(floor[:, None], FLOOR, WALL)
    # world-space tiles and courses give a sense of scale and distance
    gx = (np.abs(((pts[:, 0] + 1e4) % 64) - 32) > 30.5) | (np.abs(((pts[:, 1] + 1e4) % 64) - 32) > 30.5)
    gz = np.abs(((pts[:, 2] + 1e4) % 32) - 16) > 15.2
    base = base * np.where((floor & gx) | (~floor & gz), 0.9, 1.0)[:, None]
    lam = np.clip(normal @ SUN, 0, 1)
    lit = 0.45 + 0.55 * lam
    if shadows:
        idx = np.flatnonzero(hit & (lam > 0))
        if len(idx):
            so = pts[idx] + normal[idx] * 1.5
            sd, _ = geometry.raycaster.cast(so, np.repeat(SUN[None], len(idx), axis=0))
            lit[idx[np.isfinite(sd)]] = 0.45
    col = base * lit[:, None]
    fog = np.exp(-d / 6000.0)[:, None]
    col = col * fog + sky * (1 - fog)
    img = np.where(hit[:, None], col, sky)
    depth = np.where(hit, dist, 1e6)

    for pl in players or []:
        feet = np.asarray(pl["feet"], dtype=np.float64)
        top = np.asarray(pl["eye"], dtype=np.float64)
        if np.linalg.norm(top - eye) > 4000 or not (np.isfinite(feet).all() and np.isfinite(top).all()):
            continue
        a = feet + np.array([0, 0, 14.0])
        b = top - np.array([0, 0, 12.0])
        pd = _capsule_hits(origins, dirs, a, b, 14.0)
        hd = _capsule_hits(origins, dirs, top, top + np.array([0, 0, 1e-3]), 7.5)
        pd = np.minimum(pd, hd)
        front = np.isfinite(pd) & (pd < depth)
        if front.any():
            p = origins[front] + dirs[front] * pd[front, None]
            axis_pt = np.clip(p[:, 2], a[2], b[2])
            n = p - np.stack([np.full(len(p), a[0]), np.full(len(p), a[1]), axis_pt], axis=1)
            n /= np.maximum(np.linalg.norm(n, axis=1), 1e-6)[:, None]
            shade = 0.75 + 0.25 * np.clip(n @ SUN, 0, 1)
            img[front] = PLAYER_COLORS.get(int(pl.get("team", 0)), np.array([0.7, 0.7, 0.7])) * shade[:, None]
            depth[front] = pd[front]

    img = img.reshape(height, width, 3)
    if edges:
        dz = depth.reshape(height, width)
        nz = np.where(hit[:, None], normal, 0).reshape(height, width, 3)
        hz = hit.reshape(height, width)
        e = np.zeros((height, width), dtype=bool)
        for ax in (0, 1):
            dd = np.abs(np.diff(dz, axis=ax)) / np.minimum(np.delete(dz, 0, axis=ax), np.delete(dz, -1, axis=ax))
            nn = np.einsum("ijk,ijk->ij", np.delete(nz, 0, axis=ax), np.delete(nz, -1, axis=ax))
            both = np.delete(hz, 0, axis=ax) & np.delete(hz, -1, axis=ax)
            m = (dd > 0.06) | (both & (nn < 0.75))
            if ax == 0:
                e[1:] |= m
            else:
                e[:, 1:] |= m
        img[e] = img[e] * 0.35 + EDGE * 0.65

    if smoke is not None and t is not None:
        flat = img.reshape(-1, 3)
        far = origins + dirs * np.minimum(depth, 4000.0)[:, None]
        for it in smoke.active_at(t):
            chord = _ellipsoid_chord(origins, far, it["center"], smoke.params.core_radius, smoke.params.core_half_height)
            shell = _ellipsoid_chord(origins, far, it["center"], smoke.params.shell_radius, smoke.params.shell_half_height)
            a = np.clip(chord / 60.0, 0, 0.95)[:, None]
            s = np.clip(shell / 250.0, 0, 0.35)[:, None]
            grey = np.array([0.82, 0.82, 0.84])
            flat = flat * (1 - np.maximum(a, s)) + grey * np.maximum(a, s)
        img = flat.reshape(height, width, 3)
    return np.clip(img, 0, 1)


def project(points, eye, pitch: float, yaw: float, width: int, height: int, hfov: float = HFOV_16_9):
    """Project world points to pixel coords. Returns (x, y, in_front)."""
    p = np.atleast_2d(np.asarray(points, dtype=np.float64)) - np.asarray(eye, dtype=np.float64)
    fwd, right, up = camera_basis(pitch, yaw)
    z = p @ fwd
    x = p @ right
    y = p @ up
    tan_h = np.tan(hfov / 2 * RAD)
    tan_v = tan_h * height / width
    with np.errstate(divide="ignore", invalid="ignore"):
        sx = (x / z / tan_h + 1) / 2 * width
        sy = (1 - y / z / tan_v) / 2 * height
    return sx, sy, z > 1.0


_TOPDOWN_CACHE: dict = {}


def render_topdown(geometry: MapGeometry, ceiling_z: float | None = None, resolution: float = 8.0,
                   bounds: tuple[float, float, float, float] | None = None):
    """Hill-shaded top-down image. Returns (image[H,W,3], extent=(xmin,xmax,ymin,ymax))."""
    tris = geometry.triangles
    if tris is None:
        raise RuntimeError("map geometry unavailable")
    if bounds is None:
        mn = tris.reshape(-1, 3).min(axis=0)
        mx = tris.reshape(-1, 3).max(axis=0)
        bounds = (float(mn[0]), float(mx[0]), float(mn[1]), float(mx[1]))
    top = float(ceiling_z) if ceiling_z is not None else float(tris[..., 2].max()) + 10
    key = (id(geometry), bounds, round(top / 32) * 32, resolution)
    if key in _TOPDOWN_CACHE:
        return _TOPDOWN_CACHE[key]
    xmin, xmax, ymin, ymax = bounds
    xs = np.arange(xmin, xmax, resolution)
    ys = np.arange(ymax, ymin, -resolution)
    gx, gy = np.meshgrid(xs, ys)
    origins = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, top)], axis=1)
    dirs = np.tile(np.array([0.0, 0.0, -1.0]), (len(origins), 1))
    dist, normal = geometry.raycaster.cast(origins, dirs)
    z = np.where(np.isfinite(dist), top - dist, np.nan).reshape(gx.shape)
    zmin, zmax = np.nanpercentile(z, 2), np.nanpercentile(z, 98)
    zn = np.clip((z - zmin) / max(zmax - zmin, 1), 0, 1)
    gyz, gxz = np.gradient(np.nan_to_num(z, nan=zmin), resolution)
    shade = np.clip(0.75 + 0.25 * (-gxz * 0.6 - gyz * 0.4) / 5.0, 0.3, 1.0)
    steep = (np.abs(gxz) + np.abs(gyz)) > 3.0  # walls / ledges
    img = np.stack([0.25 + 0.55 * zn, 0.28 + 0.45 * zn, 0.32 + 0.30 * zn], axis=-1) * shade[..., None]
    img[steep] = img[steep] * 0.35
    img[np.isnan(z)] = 0.08
    out = (np.clip(img, 0, 1), (xmin, xmax, ymin, ymax))
    _TOPDOWN_CACHE[key] = out
    return out
