"""Calibration framework: empirical distributions and population baselines.

Thresholds must come from data, not from a few visually suspicious clips.
Every analysis writes raw observations to ``data/observations/<match>/``; this
module aggregates them across matches to answer questions like:

* What is the distribution of flick velocity / angular jerk?
* How often do legitimate players aim within 2 deg of a hidden opponent?
* What does hidden-tracking correlation look like for normal players?
* What is trigger-timing variance, by weapon?

and turns a corpus believed to be legitimate into ``baseline_stats`` rows that
detectors can compare against (percentiles instead of magic constants).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from cs2_analyzer.features.weapons import weapon_class
from cs2_analyzer.scoring.baselines import range_bucket, stratum_key

QUANTILES = (0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 0.999)

# (detector, metric column, optional row filter, description)
REPORT_METRICS = [
    ("snap", "peak_velocity_deg_s", None, "Flick (snap) peak angular velocity"),
    ("snap", "peak_jerk_deg_s3", None, "Flick peak angular jerk"),
    ("snap", "overshoot_deg", None, "Flick overshoot"),
    ("snap", "target_error_after_deg", None, "Flick landing error"),
    ("aim_acquisition", "reaction_ms", None, "Reaction time after first visibility"),
    ("aim_acquisition", "acquisition_ms", None, "Time to acquire target"),
    ("aim_acquisition", "peak_jerk_deg_s3", None, "Acquisition peak angular jerk"),
    ("aim_acquisition", "corrections", None, "Corrective sub-movements"),
    ("hidden_tracking", "tracking_corr", "moving_ticks >= 20", "Hidden-tracking correlation (target moving)"),
    ("hidden_tracking", "frac_within_2deg", None, "Fraction of hidden time aimed within 2 deg of the enemy"),
    ("hidden_tracking", "mean_error_deg", None, "Mean aim error to hidden enemies"),
    ("remembered_position", "advantage_2000ms_deg", None, "Current-vs-2s-old position advantage"),
    ("previsibility", "err_-250", None, "Error 250 ms before first visibility"),
    ("trigger_timing", "trigger_ms", "trigger_class == 'crosshair_moved'", "Trigger time (crosshair moved onto target)"),
    ("trigger_timing_summary", "std_ms", None, "Per-player trigger-time standard deviation"),
    ("recoil", "residual_ratio", None, "Spray residual / recoil amplitude"),
    ("recoil", "compensation_corr", None, "Recoil compensation correlation"),
    ("attraction", "visible_near_p_toward", None, "P(correction toward nearby visible enemy)"),
    ("attraction", "hidden_near_p_toward", None, "P(correction toward nearby hidden enemy)"),
    ("strategic_information", "ratio", None, "Hidden-enemy aim proximity vs time-shifted null"),
    ("mechanical_impossibility", "discrepancy_deg", None, "View-to-hit discrepancy at damage"),
]


def load_observations(obs_dir: str | Path, detector: str, match_ids: list[str] | None = None,
                      exclude_players: set[int] | None = None) -> pd.DataFrame:
    frames = []
    for f in sorted(Path(obs_dir).glob(f"*/{detector}.parquet")):
        df = pd.read_parquet(f)
        if match_ids and f.parent.name not in match_ids:
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "hidden_tracking" == detector and "ms_within_2deg" in df and "duration_ms" in df:
        df["frac_within_2deg"] = df["ms_within_2deg"] / df["duration_ms"].clip(lower=1)
    if exclude_players and "steam_id" in df:
        df = df[~df["steam_id"].isin(exclude_players)]
    return df


def describe(series: pd.Series) -> dict:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {"n": 0}
    return {"n": int(len(s)), "mean": float(s.mean()), "std": float(s.std()) if len(s) > 1 else None,
            "quantiles": {str(q): float(s.quantile(q)) for q in QUANTILES}}


def _strata(df: pd.DataFrame) -> pd.Series:
    wc = df["weapon"].map(weapon_class) if "weapon" in df else pd.Series("all", index=df.index)
    rb = df["distance_u"].map(range_bucket) if "distance_u" in df else pd.Series(None, index=df.index)
    mp = df["map"] if "map" in df else pd.Series(None, index=df.index)
    return pd.Series([stratum_key(weapon_class=a, range=b, map=c) for a, b, c in zip(wc, rb, mp)], index=df.index)


def distribution_report(obs_dir: str | Path, out_dir: str | Path, exclude_players: set[int] | None = None) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines = ["# Calibration report", "",
             "Empirical distributions of raw detector observations. Use these (from matches believed to be "
             "legitimate) to set thresholds; do not tune from individual suspicious examples.", ""]
    matches = sorted({p.parent.name for p in Path(obs_dir).glob("*/*.parquet")})
    lines.append(f"Matches in corpus: **{len(matches)}**")
    lines.append("")
    summary = {}
    for det, col, query, desc in REPORT_METRICS:
        df = load_observations(obs_dir, det, exclude_players=exclude_players)
        if df.empty or col not in df:
            continue
        if query:
            try:
                df = df.query(query)
            except Exception:
                pass
        d = describe(df[col])
        summary[f"{det}.{col}"] = d
        if d["n"] == 0:
            continue
        q = d["quantiles"]
        lines.append(f"## {desc}  (`{det}.{col}`)")
        lines.append(f"n={d['n']}, players={df['steam_id'].nunique() if 'steam_id' in df else '?'}, "
                     f"mean={d['mean']:.4g}")
        lines.append("")
        lines.append("| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |")
        lines.append("|" + "---|" * 10)
        lines.append("| " + " | ".join(f"{q[str(x)]:.4g}" for x in QUANTILES) + " |")
        if "weapon" in df and df["weapon"].notna().any():
            by = df.groupby(df["weapon"].map(weapon_class))[col].median()
            lines.append("")
            lines.append("Median by weapon class: " + ", ".join(f"{k}={v:.4g}" for k, v in by.items()))
        fig, ax = plt.subplots(figsize=(6, 3))
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        lo, hi = vals.quantile(0.005), vals.quantile(0.995)
        ax.hist(vals.clip(lo, hi), bins=60, color="#4a78b5")
        ax.set_title(desc, fontsize=9)
        img = out / f"{det}.{col}.png"
        fig.tight_layout()
        fig.savefig(img, dpi=80)
        plt.close(fig)
        lines.append("")
        lines.append(f"![{desc}]({img.name})")
        lines.append("")
    # Direct answers to recurring questions
    ht = load_observations(obs_dir, "hidden_tracking", exclude_players=exclude_players)
    if not ht.empty:
        total = ht["duration_ms"].sum()
        within2 = ht["ms_within_2deg"].sum() if "ms_within_2deg" in ht else 0
        lines.append("## How often do players aim within 2 deg of a hidden, unknown opponent?")
        lines.append(f"{within2 / max(total, 1) * 100:.2f}% of hidden-and-unknown window time "
                     f"({total / 1000:.0f} s analysed; windows overlap).")
        lines.append("")
    (out / "calibration_report.md").write_text("\n".join(lines))
    (out / "calibration_summary.json").write_text(json.dumps(summary, indent=2))
    return out / "calibration_report.md"


BASELINE_METRICS = [
    ("snap", "peak_velocity_deg_s"), ("snap", "peak_jerk_deg_s3"), ("snap", "overshoot_deg"),
    ("snap", "target_error_after_deg"), ("aim_acquisition", "reaction_ms"), ("aim_acquisition", "acquisition_ms"),
    ("hidden_tracking", "tracking_corr"), ("hidden_tracking", "mean_error_deg"),
    ("remembered_position", "advantage_2000ms_deg"), ("previsibility", "err_-250"), ("trigger_timing", "trigger_ms"),
    ("recoil", "residual_ratio"), ("recoil", "compensation_corr"), ("attraction", "visible_near_p_toward"),
    ("mechanical_impossibility", "discrepancy_deg"),
]


def build_baselines(obs_dir: str | Path, exclude_players: set[int] | None = None, min_n: int = 30) -> list[dict]:
    """Quantile baselines per metric, overall and per stratum (weapon class, range, map)."""
    rows = []
    for det, col in BASELINE_METRICS:
        df = load_observations(obs_dir, det, exclude_players=exclude_players)
        if df.empty or col not in df:
            continue
        n_matches = df["match_id"].nunique() if "match_id" in df else 0
        metric = f"{det}.{col}".replace("err_-250", "error_minus250")
        d = describe(df[col])
        if d["n"] >= min_n:
            rows.append({"metric": metric, "stratum": "all", "source_matches": n_matches, **d})
        strata = _strata(df)
        for key, sub in df.groupby(strata):
            d = describe(sub[col])
            if d["n"] >= min_n and key != "all":
                rows.append({"metric": metric, "stratum": key, "source_matches": n_matches, **d})
    return rows


def save_baselines(rows: list[dict], db=None, json_path: str | Path | None = None):
    if json_path:
        Path(json_path).write_text(json.dumps(rows, indent=2))
    if db is not None:
        from datetime import datetime, timezone

        from sqlalchemy import delete

        from cs2_analyzer.storage.models import BaselineStat

        with db.session() as s:
            s.execute(delete(BaselineStat))
            for r in rows:
                s.add(BaselineStat(metric=r["metric"], stratum=r["stratum"], n=r["n"], mean=r.get("mean"), std=r.get("std"),
                                   quantiles=r.get("quantiles"), source_matches=r.get("source_matches", 0),
                                   updated_at=datetime.now(timezone.utc)))
