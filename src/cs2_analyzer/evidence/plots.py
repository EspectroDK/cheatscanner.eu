"""Debug / reviewer figure for a single evidence event.

The figure answers: what behavior occurred, what information the player could
reasonably have had, which values were abnormal, and why the score.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from cs2_analyzer.detectors.base import EvidenceEvent  # noqa: E402
from cs2_analyzer.detectors.common import error_to_point, last_known_positions  # noqa: E402
from cs2_analyzer.evidence.render import project, render_pov, render_topdown  # noqa: E402
from cs2_analyzer.features.pair import pair_series  # noqa: E402
from cs2_analyzer.geometry.angles import bearing, unwrap_yaw, view_vector  # noqa: E402
from cs2_analyzer.geometry.visibility import LOS  # noqa: E402
from cs2_analyzer.knowledge.model import Knowledge  # noqa: E402

LOS_COLORS = {
    LOS.DIRECT_VISIBLE: "#2ca02c",
    LOS.VISIBLE_THROUGH_SMOKE: "#98df8a",
    LOS.SMOKE_OCCLUDED: "#7f7f7f",
    LOS.GEOMETRY_OCCLUDED: "#d62728",
    LOS.UNKNOWN: "#ff9896",
    LOS.NOT_APPLICABLE: "#ffffff",
}
KN_COLORS = {
    Knowledge.KNOWN: "#1f77b4",
    Knowledge.LIKELY_KNOWN: "#6baed6",
    Knowledge.POSSIBLY_KNOWN: "#c6dbef",
    Knowledge.UNKNOWN: "#000000",
    Knowledge.NOT_APPLICABLE: "#ffffff",
}


def resolve_target(result, ev: EvidenceEvent) -> int | None:
    w = result.world
    sid = ev.target_steam_id
    if sid is None and isinstance(ev.context, dict):
        for key in ("top_blocks", "fastest_samples"):
            items = ev.context.get(key) or []
            if items:
                sid = items[0].get("target_steam_id") or items[0].get("target")
                break
    if sid is not None and int(sid) in w.index_of:
        return w.index_of[int(sid)]
    o = w.index_of[ev.steam_id]
    t = w.t(ev.tick_peak)
    best, best_err = None, 1e9
    for e in w.enemies(o, t):
        if w.alive[e, t]:
            er = float(pair_series(w, o, e, t, t).err[0])
            if er < best_err:
                best, best_err = e, er
    return best


def event_window(result, ev: EvidenceEvent, max_s: float = 12.0) -> tuple[int, int, int]:
    w = result.world
    peak = w.t(ev.tick_peak)
    a = w.t(ev.tick_start) - w.ticks_for_ms(1000)
    b = w.t(ev.tick_end) + w.ticks_for_ms(500)
    half = w.ticks_for_ms(max_s * 1000 / 2)
    a = max(a, peak - half, 0)
    b = min(b, peak + half, w.T - 1)
    return a, peak, b


def _strip(ax, x, codes, colors, label):
    for code, col in colors.items():
        m = codes == int(code)
        if m.any():
            ax.fill_between(x, 0, 1, where=m, color=col, step="mid", linewidth=0)
    ax.set_yticks([])
    ax.set_ylabel(label, rotation=0, ha="right", va="center", fontsize=8)


def plot_event(result, ev: EvidenceEvent, path: str | Path) -> Path:
    w = result.world
    o = w.index_of[ev.steam_id]
    e = resolve_target(result, ev)
    a, peak, b = event_window(result, ev)
    x = (np.arange(a, b + 1) - peak) * 1000.0 / w.tickrate

    fig = plt.figure(figsize=(18, 12))
    gs = fig.add_gridspec(6, 3, height_ratios=[3.2, 1, 1, 1, 0.35, 0.35], hspace=0.35, wspace=0.18)
    ax_map = fig.add_subplot(gs[0, 0])
    ax_pov = fig.add_subplot(gs[0, 1])
    ax_txt = fig.add_subplot(gs[0, 2])
    ax_err = fig.add_subplot(gs[1, :])
    ax_brg = fig.add_subplot(gs[2, :], sharex=ax_err)
    ax_kin = fig.add_subplot(gs[3, :], sharex=ax_err)
    ax_los = fig.add_subplot(gs[4, :], sharex=ax_err)
    ax_kn = fig.add_subplot(gs[5, :], sharex=ax_err)

    # --- top-down map
    if result.geometry.available:
        z = float(np.nanmax(w.eye[o, a:b + 1, 2])) + 150
        img, ext = render_topdown(result.geometry, ceiling_z=z)
        ax_map.imshow(img, extent=ext, origin="upper")
    pts = [w.pos[o, a:b + 1, :2]]
    ax_map.plot(w.pos[o, a:b + 1, 0], w.pos[o, a:b + 1, 1], "-", color="cyan", lw=2, label="suspect")
    step = max(1, (b - a) // 12)
    for t in range(a, b + 1, step):
        d = view_vector(w.pitch[o, t], w.yaw[o, t])
        ax_map.plot([w.pos[o, t, 0], w.pos[o, t, 0] + d[0] * 350], [w.pos[o, t, 1], w.pos[o, t, 1] + d[1] * 350],
                    "-", color="yellow", lw=0.8, alpha=0.8)
    if e is not None:
        codes = result.vis.los[o, e, a:b + 1]
        for t in range(a, b + 1):
            ax_map.plot(w.pos[e, t, 0], w.pos[e, t, 1], ".", ms=3, color=LOS_COLORS.get(LOS(int(codes[t - a])), "k"))
        pts.append(w.pos[e, a:b + 1, :2])
        ax_map.plot(w.pos[e, peak, 0], w.pos[e, peak, 1], "o", mfc="none", mec="magenta", ms=12, mew=2)
    ax_map.plot(w.pos[o, peak, 0], w.pos[o, peak, 1], "o", color="cyan", ms=8)
    allp = np.concatenate(pts)
    allp = allp[np.isfinite(allp).all(axis=1)]
    if len(allp):
        c = allp.mean(axis=0)
        r = max(500.0, float(np.abs(allp - c).max()) + 250)
        ax_map.set_xlim(c[0] - r, c[0] + r)
        ax_map.set_ylim(c[1] - r, c[1] + r)
    ax_map.set_title("Top-down (suspect cyan + view rays; target coloured by LOS)", fontsize=9)
    ax_map.legend(handles=[Patch(color=LOS_COLORS[s], label=s.name) for s in
                           (LOS.DIRECT_VISIBLE, LOS.GEOMETRY_OCCLUDED, LOS.SMOKE_OCCLUDED, LOS.UNKNOWN)],
                  fontsize=6, loc="lower left")
    ax_map.set_aspect("equal")

    # --- POV at peak
    W, H = 480, 270
    img = render_pov(result.geometry, w.eye[o, peak], float(w.pitch[o, peak]), float(w.yaw[o, peak]), W, H,
                     smoke=result.smoke, t=peak)
    ax_pov.imshow(img)
    ax_pov.plot([W / 2 - 8, W / 2 + 8], [H / 2, H / 2], "-", color="lime", lw=1.2)
    ax_pov.plot([W / 2, W / 2], [H / 2 - 8, H / 2 + 8], "-", color="lime", lw=1.2)
    if e is not None:
        bp = w.body_points(e, np.array([peak]))
        sx, sy, front = project(np.array([bp["head"][0], bp["knee"][0]]), w.eye[o, peak], float(w.pitch[o, peak]),
                                float(w.yaw[o, peak]), W, H)
        if front.all():
            hidden = int(result.vis.los[o, e, peak]) not in (LOS.DIRECT_VISIBLE, LOS.VISIBLE_THROUGH_SMOKE)
            hgt = abs(sy[1] - sy[0]) * 1.25 + 4
            ax_pov.add_patch(plt.Rectangle((sx[0] - hgt * 0.18, sy[0] - hgt * 0.1), hgt * 0.36, hgt, fill=False,
                                           ec="magenta", lw=1.5, ls="--" if hidden else "-"))
            if hidden:
                ax_pov.text(4, H - 6, "Magenta outline = REVIEWER ANNOTATION (target NOT visible to player)",
                            color="magenta", fontsize=7, va="bottom")
    ax_pov.set_xlim(0, W)
    ax_pov.set_ylim(H, 0)
    ax_pov.set_axis_off()
    ax_pov.set_title(f"Collision-mesh POV at peak (tick {ev.tick_peak})", fontsize=9)

    # --- text
    ax_txt.set_axis_off()
    lines = [
        f"{ev.detector_type}   [{ev.evidence_axis}]",
        f"player {w.names[o]} ({ev.steam_id})",
        f"target {w.names[e] if e is not None else '-'}   round {ev.round_number}",
        f"ticks {ev.tick_start}..{ev.tick_end} (peak {ev.tick_peak})",
        f"severity {ev.severity:.2f}  reliability {ev.reliability:.2f}  info-conf {ev.information_confidence:.2f}",
        f"weight (confidence) {ev.confidence:.3f}",
        "",
    ]
    lines += textwrap.wrap(ev.explanation, 70)
    lines.append("")
    for k, v in list((ev.metrics or {}).items())[:22]:
        if isinstance(v, (dict, list)):
            v = str(v)[:60]
        elif isinstance(v, float):
            v = f"{v:.3g}"
        lines.append(f"{k}: {v}")
    ax_txt.text(0, 1, "\n".join(lines), va="top", family="monospace", fontsize=7.2)

    # --- timelines
    if e is not None:
        ps = pair_series(w, o, e, a, b)
        ax_err.plot(x, ps.err, color="k", lw=1.2, label="error to target (current)")
        lk, has = last_known_positions(result_ctx(result), o, e, ps.t)
        if has.any():
            ax_err.plot(x, np.where(has, error_to_point(w, o, ps.t, lk), np.nan), color="tab:orange", lw=1,
                        label="error to last-known position")
        ax_err.set_ylim(0, min(60, np.nanmax(ps.err[np.isfinite(ps.err)]) * 1.1 if np.isfinite(ps.err).any() else 60))
        ax_brg.plot(x, unwrap_yaw(ps.target_yaw), color="magenta", label="enemy bearing (yaw)")
        tu = unwrap_yaw(ps.target_yaw)
        ay = unwrap_yaw(w.yaw[o, a:b + 1])
        ay = ay + np.round((np.nanmean(tu) - np.nanmean(ay)) / 360.0) * 360.0
        ax_brg.plot(x, ay, color="tab:blue", label="aim (yaw)")
        tgt_only = np.nancumsum(np.nan_to_num(ps.d_bearing_target_yaw)) + tu[0]
        ax_brg.plot(x, tgt_only, color="magenta", ls=":", lw=1, label="bearing change from enemy movement only")
        _strip(ax_los, x, result.vis.los[o, e, a:b + 1], LOS_COLORS, "LOS")
        _strip(ax_kn, x, result.knowledge.level[o, e, a:b + 1], KN_COLORS, "knowledge")
    ax_err.set_ylabel("deg")
    ax_err.legend(fontsize=7, loc="upper right")
    ax_brg.set_ylabel("deg")
    ax_brg.legend(fontsize=7, loc="upper right")
    speed = np.full(b - a + 1, np.nan)
    from cs2_analyzer.geometry.angles import angular_distance, smooth

    speed[1:] = angular_distance(w.pitch[o, a + 1:b + 1], w.yaw[o, a + 1:b + 1], w.pitch[o, a:b], w.yaw[o, a:b]) * w.tickrate
    acc = np.gradient(smooth(np.nan_to_num(speed), 3), w.dt)
    jerk = np.gradient(smooth(acc, 3), w.dt)
    ax_kin.plot(x, speed, color="tab:green", label="angular velocity (deg/s)")
    k2 = ax_kin.twinx()
    k2.plot(x, acc / 1000, color="tab:purple", lw=0.8, label="accel (k deg/s²)")
    k2.plot(x, jerk / 100000, color="tab:gray", lw=0.6, label="jerk (100k deg/s³)")
    k2.legend(fontsize=6, loc="upper left")
    ax_kin.legend(fontsize=7, loc="upper right")
    for axx in (ax_err, ax_brg, ax_kin):
        for t in np.nonzero(w.shot_mask[o, a:b + 1])[0]:
            axx.axvline(x[t], color="red", lw=0.6, alpha=0.6)
        axx.axvline(0, color="k", lw=0.8, ls="--")
        axx.axvspan((w.t(ev.tick_start) - peak) * 1000 / w.tickrate, (w.t(ev.tick_end) - peak) * 1000 / w.tickrate,
                    color="yellow", alpha=0.08)
    ax_kn.set_xlabel("ms relative to peak (red lines = shots, yellow = evidence window)")
    ax_kn.legend(handles=[Patch(color=KN_COLORS[s], label=s.name) for s in
                          (Knowledge.KNOWN, Knowledge.LIKELY_KNOWN, Knowledge.POSSIBLY_KNOWN, Knowledge.UNKNOWN)],
                 fontsize=6, ncol=4, loc="lower right", bbox_to_anchor=(1, -2.2))
    fig.suptitle(f"Evidence {ev.id} - {ev.detector_type} - match {ev.match_id}  (reviewer view; not a verdict)", fontsize=11)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=80, bbox_inches="tight")
    plt.close(fig)
    return path


class _Ctx:
    def __init__(self, result):
        self.world = result.world
        self.knowledge = result.knowledge


def result_ctx(result):
    return _Ctx(result)
