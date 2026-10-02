"""Evidence clips (MP4) rendered from demo data + collision mesh.

Controlling the CS2 client to record real footage is not required for the MVP.
Instead each clip is a reviewer reconstruction:

* left: the suspect's first-person view rendered from the collision mesh with
  their exact eye position and view angles, crosshair, and a reviewer-only
  enemy outline (dashed + labelled when the enemy was NOT visible to them);
* right: top-down position view and live metrics overlay (detector, LOS,
  time unseen, aim error, tracking correlation, sound, team info).

The on-screen banner states that outlines are reviewer annotations.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from cs2_analyzer.detectors.base import EvidenceEvent  # noqa: E402
from cs2_analyzer.evidence.plots import LOS_COLORS, resolve_target  # noqa: E402
from cs2_analyzer.evidence.render import project, render_pov, render_topdown  # noqa: E402
from cs2_analyzer.features.pair import pair_series  # noqa: E402
from cs2_analyzer.geometry.angles import view_vector  # noqa: E402
from cs2_analyzer.geometry.mesh import MapGeometry  # noqa: E402
from cs2_analyzer.geometry.visibility import LOS  # noqa: E402


def poster_path(clip: str | Path) -> Path:
    """Still frame at the event's peak, saved next to the clip."""
    return Path(clip).with_suffix(".jpg")


_RENDER_GEOMETRY: dict = {}


def render_geometry(result, cfg: dict) -> MapGeometry:
    """The mesh to draw: the game-built one from ``render_maps_dir`` when present, else the analysis mesh."""
    geo = result.geometry
    folder = cfg.get("render_maps_dir")
    name = getattr(geo, "map_name", None)
    if not folder or not name:
        return geo
    # The file's modification time is part of the key, so a mesh pushed to a running server is picked up.
    stamps = []
    for f in (Path(folder) / f"{name}.tri", Path(folder) / f"{name.removeprefix('de_')}.tri"):
        try:
            stamps.append(f.stat().st_mtime_ns)
        except OSError:
            stamps.append(None)
    key = (str(folder), name, tuple(stamps))
    if key not in _RENDER_GEOMETRY:
        for old in [k for k in _RENDER_GEOMETRY if k[:2] == key[:2]]:
            del _RENDER_GEOMETRY[old]
        cand = MapGeometry.load(name, folder)
        _RENDER_GEOMETRY[key] = cand if cand.available else None
    return _RENDER_GEOMETRY[key] or geo


def flash_alpha(remaining: float) -> float:
    """How white the view is: fully blind for most of the flash, fading out over the last second."""
    return 0.9 * float(np.clip(remaining, 0.0, 1.0))


def render_clip(result, ev: EvidenceEvent, path: str | Path, cfg: dict) -> Path | None:
    import imageio_ffmpeg

    w = result.world
    o = w.index_of[ev.steam_id]
    e = resolve_target(result, ev)
    peak = w.t(ev.tick_peak)
    a = max(0, peak - w.ticks_for_ms(float(cfg.get("clip_seconds_before", 6.0)) * 1000))
    b = min(w.T - 1, peak + w.ticks_for_ms(float(cfg.get("clip_seconds_after", 4.0)) * 1000))
    fps = int(cfg.get("clip_fps", 32))
    step = max(1, int(round(w.tickrate / fps)))
    W, H = int(cfg.get("clip_width", 960)), int(cfg.get("clip_height", 540))
    pov_w = int(cfg.get("clip_pov_width", 960))
    pov_h = pov_w * 9 // 16
    geo = render_geometry(result, cfg)
    metrics = ev.metrics or {}
    corr = metrics.get("tracking_corr")

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    ax_pov = fig.add_axes([0.0, 0.25, 0.667, 0.75])
    ax_map = fig.add_axes([0.672, 0.40, 0.325, 0.58])
    ax_txt = fig.add_axes([0.0, 0.0, 1.0, 0.24])
    for ax in (ax_pov, ax_map, ax_txt):
        ax.set_axis_off()

    # static map background, cropped so every player alive during the clip stays in view (a radar)
    alive_any = w.alive[:, a:b + 1].any(axis=1)
    pts = [w.pos[q, a:b + 1, :2][w.alive[q, a:b + 1]] for q in range(w.P) if alive_any[q]]
    pts += [w.pos[o, a:b + 1, :2]] + ([w.pos[e, a:b + 1, :2]] if e is not None else [])
    allp = np.concatenate(pts)
    allp = allp[np.isfinite(allp).all(axis=1)]
    c = allp.mean(axis=0) if len(allp) else np.zeros(2)
    r = max(600.0, float(np.abs(allp - c).max()) + 300) if len(allp) else 800.0
    if geo.available:
        zc = float(np.nanmax(w.eye[o, a:b + 1, 2])) + 150
        img, ext = render_topdown(geo, ceiling_z=zc, bounds=(c[0] - r, c[0] + r, c[1] - r, c[1] + r),
                                  resolution=max(4.0, 2 * r / 450))
        ax_map.imshow(img, extent=ext, origin="upper")
    ax_map.set_xlim(c[0] - r, c[0] + r)
    ax_map.set_ylim(c[1] - r, c[1] + r)
    # everyone else: team-coloured dots with a short line where they look
    radar_q = [q for q in range(w.P) if q not in (o, e) and alive_any[q]]
    team_col = {2: "#f0b23a", 3: "#4f95e8"}
    oth_views = LineCollection([], colors="white", linewidths=0.8, alpha=0.7)
    ax_map.add_collection(oth_views)
    oth_dots = ax_map.scatter([], [], s=22, edgecolors="black", linewidths=0.5, zorder=3)
    (sus_path,) = ax_map.plot([], [], color="cyan", lw=1)
    (sus_view,) = ax_map.plot([], [], color="yellow", lw=1.2)
    (sus_dot,) = ax_map.plot([], [], "o", color="cyan", ms=5)
    (tgt_path,) = ax_map.plot([], [], color="magenta", lw=0.8, alpha=0.6)
    (tgt_dot,) = ax_map.plot([], [], "o", ms=6, mec="magenta")

    pov_img = ax_pov.imshow(np.zeros((pov_h, pov_w, 3)), interpolation="antialiased")
    ax_pov.plot([pov_w / 2 - 10, pov_w / 2 + 10], [pov_h / 2] * 2, color="lime", lw=1.3)
    ax_pov.plot([pov_w / 2] * 2, [pov_h / 2 - 10, pov_h / 2 + 10], color="lime", lw=1.3)
    outline = plt.Rectangle((0, 0), 1, 1, fill=False, ec="magenta", lw=1.1, visible=False)
    ax_pov.add_patch(outline)
    ax_pov.set_xlim(0, pov_w)
    ax_pov.set_ylim(pov_h, 0)
    ax_pov.text(6, 16, "REVIEWER RECONSTRUCTION - magenta outline is an annotation, NOT what the player saw",
                color="magenta", fontsize=7, bbox=dict(fc="white", alpha=0.6, lw=0))
    shot_txt = ax_pov.text(pov_w - 70, 20, "", color="red", fontsize=12, weight="bold")
    dead_txt = ax_pov.text(pov_w / 2, pov_h / 2, "", ha="center", color="white", fontsize=14)
    flash_txt = ax_pov.text(pov_w / 2, pov_h * 0.12, "", ha="center", va="center", color="#1a1206", fontsize=13,
                            weight="bold", bbox=dict(fc="#f0c040", ec="none", boxstyle="round,pad=0.35"), visible=False)
    info_txt = ax_txt.text(0.01, 0.95, "", va="top", family="monospace", fontsize=8.5, transform=ax_txt.transAxes)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(str(path), (W, H), fps=fps, codec="libx264", pix_fmt_out="yuv420p",
                                         macro_block_size=1, output_params=["-crf", "23"])
    writer.send(None)
    poster_saved = False
    try:
        for t in range(a, b + 1, step):
            alive = bool(w.alive[o, t])
            dead_txt.set_text("" if alive else "suspect dead")
            outline.set_visible(False)
            flash_txt.set_visible(False)
            if alive:
                others = [{"feet": w.pos[q, t], "eye": w.eye[q, t], "team": int(w.team[q, t])}
                          for q in range(w.P) if q != o and w.alive[q, t]]
                view = render_pov(geo, w.eye[o, t], float(w.pitch[o, t]), float(w.yaw[o, t]),
                                  pov_w, pov_h, smoke=result.smoke, t=t, players=others)
                blind = float(w.flash_remaining[o, t])
                if blind > 0:
                    view = view * (1 - flash_alpha(blind)) + flash_alpha(blind)
                flash_txt.set_text(f"FLASHED  {blind:.1f} s")
                flash_txt.set_visible(blind > 0.05)
                pov_img.set_data(view)
                if e is not None and w.alive[e, t]:
                    bp = w.body_points(e, np.array([t]))
                    sx, sy, front = project(np.array([bp["head"][0], bp["knee"][0]]), w.eye[o, t], float(w.pitch[o, t]),
                                            float(w.yaw[o, t]), pov_w, pov_h)
                    if front.all() and np.isfinite(sx).all():
                        hidden = int(result.vis.los[o, e, t]) not in (LOS.DIRECT_VISIBLE, LOS.VISIBLE_THROUGH_SMOKE)
                        hgt = abs(sy[1] - sy[0]) * 1.25 + 4
                        outline.set_bounds(sx[0] - hgt * 0.24, sy[0] - hgt * 0.12, hgt * 0.48, hgt * 1.04)
                        outline.set_linestyle("--" if hidden else "-")
                        outline.set_visible(True)
            shot_txt.set_text("SHOT" if w.shot_mask[o, t:t + step].any() else "")
            sus_path.set_data(w.pos[o, a:t + 1, 0], w.pos[o, a:t + 1, 1])
            d = view_vector(w.pitch[o, t], w.yaw[o, t])
            sus_view.set_data([w.pos[o, t, 0], w.pos[o, t, 0] + d[0] * 400], [w.pos[o, t, 1], w.pos[o, t, 1] + d[1] * 400])
            sus_dot.set_data([w.pos[o, t, 0]], [w.pos[o, t, 1]])
            live = [q for q in radar_q if w.alive[q, t] and np.isfinite(w.pos[q, t]).all()]
            if live:
                xy = w.pos[live, t, :2]
                dv = view_vector(w.pitch[live, t], w.yaw[live, t])[:, :2]
                oth_dots.set_offsets(xy)
                oth_dots.set_facecolors([team_col.get(int(w.team[q, t]), "#bbbbbb") for q in live])
                oth_views.set_segments([[p0, p0 + v * 160] for p0, v in zip(xy, dv)])
            else:
                oth_dots.set_offsets(np.zeros((0, 2)))
                oth_views.set_segments([])
            rel = (t - peak) / w.tickrate
            lines = [f"{ev.detector_type.replace('_', ' ').title()}   |   {w.names[o]}   |   round {ev.round_number}   |   "
                     f"t = {rel:+.2f}s from peak   |   tick {w.tick(t)}"
                     + (f"   |   FLASHED {float(w.flash_remaining[o, t]):.1f}s" if w.flash_remaining[o, t] > 0.05 else "")]
            if e is not None:
                los = LOS(int(result.vis.los[o, e, t]))
                tgt_path.set_data(w.pos[e, a:t + 1, 0], w.pos[e, a:t + 1, 1])
                tgt_dot.set_data([w.pos[e, t, 0]], [w.pos[e, t, 1]])
                tgt_dot.set_color(LOS_COLORS.get(los, "k"))
                kd = result.knowledge.describe(o, e, t)
                err = float(pair_series(w, o, e, t, t).err[0])
                unseen = kd["last_seen_ms"]
                lines.append(
                    f"LOS: {los.name:<22} knowledge: {kd['knowledge']:<15} "
                    f"target unseen: {'-' if unseen is None else f'{unseen / 1000:.2f}s':<7} aim error: {err:5.1f} deg"
                )
                lines.append(
                    f"possible sound: {'YES' if kd['possible_sound'] else 'NO':<4} team info: "
                    f"{'YES' if kd['teammate_los'] else 'NO':<4} tracking correlation (window): "
                    f"{'-' if corr is None else f'{corr:.2f}'}   severity {ev.severity:.2f} / reliability {ev.reliability:.2f}"
                )
            info_txt.set_text("\n".join(lines))
            fig.canvas.draw()
            frame = np.asarray(fig.canvas.buffer_rgba())[..., :3]
            if frame.shape[:2] != (H, W):
                pad = np.zeros((H, W, 3), dtype=np.uint8)
                hh, ww = min(H, frame.shape[0]), min(W, frame.shape[1])
                pad[:hh, :ww] = frame[:hh, :ww]
                frame = pad
            writer.send(np.ascontiguousarray(frame))
            if not poster_saved and t >= peak:
                # The website shows this frame (the flagged moment) as the video's thumbnail.
                Image.fromarray(frame).save(poster_path(path), quality=82)
                poster_saved = True
    finally:
        writer.close()
        plt.close(fig)
    return path
