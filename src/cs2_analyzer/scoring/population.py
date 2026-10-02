"""Match-level comparison of a player with a clean reference population.

Single evidence events rarely separate cheaters from legitimate players: on
the CS2CD Mirage data, labelled cheaters produce extreme single events only
slightly more often than clean players. What does separate them is their
*typical* behaviour over a match: how fast they acquire targets, how close
their aim stays to enemies they cannot see, and so on (docs/validation/cs2cd).

For each metric below, a player's match median is placed in the distribution
of per-player medians of a clean reference population (oriented so that a high
percentile is always the suspicious direction). The combined percentile is the
mean over the metrics the player has enough data for (at least two).

This is reported next to the evidence score and does **not** change the
classification: it is a population comparison, not evidence of any specific
play. The reference is per map for the nine CS2CD maps and pooled over them for
any other map.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd

# (detector, column, sign, row filter). sign +1: higher values are the
# suspicious direction; -1: lower values are. Chosen on half of the CS2CD
# cheater matches and validated on the other half, then restricted to metrics
# on which 15 pro Mirage matches look like ordinary clean players: faster
# acquisition and pre-visibility convergence also separate cheaters, but pros
# show them just as strongly, so they measure skill as much as cheating
# (docs/validation/cs2cd).
METRICS = [
    ("hidden_tracking", "mean_error_deg", -1, None),
    ("mechanical_impossibility", "discrepancy_deg", +1, None),
    ("recoil", "residual_ratio", +1, None),
]

DEFAULT_REFERENCE = "population_reference_cs2cd_all.json"


def _key(det: str, col: str) -> str:
    return f"{det}.{col}"


def player_medians(frames: dict[str, pd.DataFrame], min_obs: int) -> pd.DataFrame:
    """Per-player medians of each metric (NaN where fewer than ``min_obs`` observations)."""
    cols = {}
    for det, col, _sign, query in METRICS:
        df = frames.get(det)
        if df is None or df.empty or col not in df or "steam_id" not in df:
            continue
        if query:
            df = df.query(query)
        vals = pd.to_numeric(df[col], errors="coerce")
        g = vals.groupby(df["steam_id"]).agg(["median", "count"])
        cols[_key(det, col)] = g["median"].where(g["count"] >= min_obs)
    out = pd.DataFrame(cols)
    out.index.name = "steam_id"
    return out


def build_reference(obs_dir: str | Path, min_obs: int = 5, exclude_players: set[int] | None = None,
                    source: str = "") -> dict:
    """Reference distributions of per-player medians from an observations directory."""
    from cs2_analyzer.calibration import load_observations

    frames = {det: load_observations(obs_dir, det, exclude_players=exclude_players) for det, *_ in METRICS}
    med = player_medians(frames, min_obs)
    n_matches = len({p.parent.name for p in Path(obs_dir).glob("*/*.parquet")})
    ref = {"source": source or str(obs_dir), "matches": n_matches, "min_obs": min_obs, "metrics": {}}
    for det, col, sign, _q in METRICS:
        k = _key(det, col)
        if k in med:
            v = np.sort(med[k].dropna().to_numpy(dtype=float))
            ref["metrics"][k] = {"sign": sign, "n_players": int(len(v)), "values": [round(float(x), 5) for x in v]}
    return ref


def load_reference(path: str | Path | None = None, map_name: str | None = None) -> dict | None:
    """Load a reference file; ``None``/empty uses the bundled CS2CD reference for ``map_name``
    (its own where the dataset has enough clean players on that map, else pooled over all maps)."""
    from cs2_analyzer.scoring.player_evidence import _load_json

    return _load_json(path, map_name, "population_reference_cs2cd_{map}.json", DEFAULT_REFERENCE)


def percentiles(frames: dict[str, pd.DataFrame], reference: dict, min_obs: int | None = None,
                min_metrics: int = 2) -> dict[int, dict]:
    """Per player: oriented percentile of each metric vs the reference, and their mean."""
    min_obs = int(min_obs if min_obs is not None else reference.get("min_obs", 5))
    med = player_medians(frames, min_obs)
    out: dict[int, dict] = {}
    for sid, row in med.iterrows():
        per = {}
        for k, spec in reference.get("metrics", {}).items():
            v = row.get(k)
            ref = np.asarray(spec["values"], dtype=float)
            if v is None or pd.isna(v) or not len(ref):
                continue
            below = np.searchsorted(ref, v * 1.0, side="left")
            upto = np.searchsorted(ref, v * 1.0, side="right")
            pct = (below + upto) / 2 / len(ref)  # mid-rank percentile, ties split
            per[k] = {"median": round(float(v), 4), "percentile": round(float(pct if spec["sign"] > 0 else 1 - pct), 4)}
        combined = float(np.mean([p["percentile"] for p in per.values()])) if len(per) >= min_metrics else None
        out[int(sid)] = {"combined_percentile": None if combined is None else round(combined, 4),
                         "metrics_used": len(per), "metrics": per}
    return out
