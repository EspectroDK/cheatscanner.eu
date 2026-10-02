"""Evidence preservation: JSON per player, debug plots, clips, HTML report.

Everything a reviewer needs to understand a flag is written *before* the raw
demo is deleted: match metadata, per-player assessment, all evidence events
with metrics/context/explanation, versions, and plots/clips for the top events
of flagged players.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path

from cs2_analyzer.detectors.base import jsonable

log = logging.getLogger("cs2_analyzer")


def write_evidence(result, config, *, debug: bool, generate_evidence: bool, say=print):
    cfg = config.section("evidence")
    top_n = int(cfg.get("top_events_per_player", 5))
    flagged_classes = set(cfg.get("generate_for_classes", ["HIGH", "VERY_HIGH"]))
    out = result.output_dir
    clip_all = bool(cfg.get("clip_all_events", True))
    max_clips = int(cfg.get("max_clips_per_match", 30))
    report_rows = []

    # Which events get plots/clips: the top events of flagged players, and (clip_all_events) the top
    # events of every other player too, since a single suspicious moment is worth a look even when the
    # player's overall class stays NORMAL. Capped per match, strongest events first.
    per_player = {}
    wanted = []
    for sid, ass in result.assessments.items():
        events = sorted([e for e in result.events if e.steam_id == sid], key=lambda e: -e.confidence)
        per_player[sid] = events
        if generate_evidence and (clip_all or ass.classification in flagged_classes):
            wanted.extend(events[:top_n])
    wanted = sorted(wanted, key=lambda e: -e.confidence)[:max_clips]
    if debug:
        wanted = sorted({id(e): e for e in wanted + [e for ev in per_player.values() for e in ev[:top_n]]}.values(),
                        key=lambda e: -e.confidence)

    for ev in wanted:
        p = result.world.index_of[ev.steam_id]
        pdir = out / str(ev.steam_id)
        from cs2_analyzer.evidence.plots import plot_event

        try:
            ev.debug_plot_path = str(plot_event(result, ev, pdir / "plots" / f"{ev.id}.png"))
        except Exception as exc:  # plotting must never fail an analysis
            log.warning("plot failed for %s: %s", ev.id, exc)
        if generate_evidence and result.geometry.available:
            from cs2_analyzer.evidence.clips import render_clip

            say(f"Rendering evidence clip {ev.id} ({ev.detector_type}) for {result.world.names[p]} ...")
            try:
                ev.video_path = str(render_clip(result, ev, pdir / "clips" / f"{ev.id}.mp4", cfg))
            except Exception as exc:
                log.warning("clip failed for %s: %s", ev.id, exc)

    for sid, ass in result.assessments.items():
        p = result.world.index_of[sid]
        events = per_player[sid]
        pdir = out / str(sid)
        pdir.mkdir(parents=True, exist_ok=True)
        top = events[:top_n]
        doc = {
            "player": {"steam_id": str(sid), "name": result.world.names[p]},
            "match_id": result.meta.match_id,
            "map": result.meta.map_name,
            "assessment": ass.to_dict(),
            "fingerprint": result.fingerprints.get(sid),
            "behavior_shift": result.behavior_shifts.get(sid),
            "evidence_events": [e.to_dict() for e in events],
            "versions": {"parser": f"{result.meta.parser_name} {result.meta.parser_version}",
                         "detector": events[0].detector_version if events else None, "scoring": ass.model_version},
            "disclaimer": "Behavioral evidence for human review. Not a verdict.",
        }
        (pdir / "evidence.json").write_text(json.dumps(jsonable(doc), indent=2, ensure_ascii=False), encoding="utf-8")
        report_rows.append((sid, result.world.names[p], ass, top))
    write_report(result, report_rows)


def write_report(result, rows):
    out = result.output_dir
    parts = [
        "<!doctype html><meta charset='utf-8'><title>CS2 evidence report</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:1300px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px;font-size:13px}th{background:#f3f3f3}"
        ".ev{border:1px solid #ddd;padding:8px;margin:10px 0}.ev img{max-width:100%}.muted{color:#666}</style>",
        f"<h1>Match {html.escape(result.meta.match_id)} - {html.escape(result.meta.map_name)}</h1>",
        "<p class='muted'>Evidence/anomaly scores for human review. Thresholds are UNCALIBRATED. "
        "Not a verdict and not a probability of cheating.</p>",
        "<table><tr><th>Player</th><th>Steam ID</th><th>Class</th><th>Score</th><th>Info</th><th>Aim</th>"
        "<th>Timing</th><th>Recoil</th><th>Events</th><th>Encounters</th></tr>",
    ]
    for sid, name, ass, _ in sorted(rows, key=lambda r: -r[2].overall):
        ax = ass.axis_scores
        parts.append(
            f"<tr><td><a href='#p{sid}'>{html.escape(name)}</a></td><td>{sid}</td><td>{ass.classification}</td>"
            f"<td>{ass.overall:.2f}</td><td>{ax.get('HIDDEN_INFORMATION', 0):.2f}</td><td>{ax.get('AIM_MECHANICS', 0):.2f}</td>"
            f"<td>{ax.get('SHOT_TIMING', 0):.2f}</td><td>{ax.get('RECOIL', 0):.2f}</td><td>{ass.evidence_event_count}</td>"
            f"<td>{ass.encounters_analyzed}</td></tr>"
        )
    parts.append("</table>")
    for sid, name, ass, top in rows:
        parts.append(f"<h2 id='p{sid}'>{html.escape(name)} ({sid}) - {ass.classification} {ass.overall:.2f}</h2>")
        if ass.notes:
            parts.append("<ul>" + "".join(f"<li class='muted'>{html.escape(n)}</li>" for n in ass.notes) + "</ul>")
        for ev in top:
            parts.append(
                f"<div class='ev'><b>{html.escape(ev.detector_type)}</b> round {ev.round_number}, ticks {ev.tick_start}-"
                f"{ev.tick_end} &middot; severity {ev.severity:.2f} &middot; reliability {ev.reliability:.2f} &middot; "
                f"info-conf {ev.information_confidence:.2f}<br>{html.escape(ev.explanation)}"
            )
            if ev.debug_plot_path:
                rel = Path(ev.debug_plot_path).relative_to(out)
                parts.append(f"<br><a href='{rel}'><img src='{rel}' loading='lazy'></a>")
            if ev.video_path:
                rel = Path(ev.video_path).relative_to(out)
                parts.append(f"<br><video controls width='960' src='{rel}'></video>")
            parts.append("</div>")
    (out / "report.html").write_text("\n".join(parts), encoding="utf-8")
