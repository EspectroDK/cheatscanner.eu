"""Per-player evidence accumulated over all of a player's observations in a match.

Single evidence events rarely separate cheaters from legitimate players, while
a player's *typical* behaviour over a match does (docs/validation/cs2cd). Other
behavioural anti-cheats score the player over many shots for the same reason
(VACnet reads about 140 shots per verdict, BotScreen waits for several flagged
shots in a row).

Every observation of the features below is turned into a log-likelihood ratio

    llr(x) = log P(x | labelled cheater) / P(x | clean player)

from binned rates in a labelled corpus (bins are deciles of the clean
distribution, Laplace-smoothed, clipped). Per player and feature the mean
llr is multiplied by min(n, ``cap``): more observations add evidence, but one
player's habits repeat, so the weight stops growing after ``cap`` of them.
The player score is the sum over features. It is compared with the scores of
clean players (computed out-of-fold) and mapped to an evidence strength that
enters the match assessment as the ``profile`` family (scoring/aggregate.py).

The llr tables are fitted on all nine CS2CD maps together; the clean reference
scores are per map (``player_evidence_cs2cd_<map>.json``), so the clean 98th
percentile means the same on every map. Maps outside the dataset use the model
pooled over all nine (docs/validation/cs2cd/per_map.md).

The features were chosen on held-out CS2CD matches and checked against 15 pro
Mirage matches: faster target acquisition and pre-visibility convergence also
separate cheaters, but pros show them as strongly, so they measure skill and
are left out.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd

# (detector, column, row filter)
FEATURES = [
    ("hidden_tracking", "mean_error_deg", None),
    ("hidden_tracking", "frac_within_2deg", None),
    ("mechanical_impossibility", "discrepancy_deg", None),
    ("trigger_timing", "trigger_ms", None),
    ("aim_acquisition", "peak_jerk_deg_s3", None),
]

# One value per player and match: (detector, column, fixed bin edges). Counted as a
# single observation (weight 1), and not towards ``min_features``.
# lattice_fit: share of the player's idle view changes that are whole mouse counts
# (detectors/input_lattice.py). Off the lattice (< 0.8): 5.9% of labelled cheaters, 1.0% of
# clean players (controllers and mid-match sensitivity changes do it too).
PLAYER_FEATURES = [
    ("input_lattice_summary", "lattice_fit", [0.8, 0.95]),
]

DEFAULT_MODEL = "player_evidence_cs2cd_all.json"  # pooled over every dataset map
DEFAULTS = {"bins": 10, "cap": 30, "clip": 2.0, "min_obs": 3, "min_features": 2}
# clean-score percentile -> strength; ELEVATED (0.25) at the clean 98th percentile.
# Capped below HIGH: a profile alone never goes above ELEVATED.
DEFAULT_STRENGTH = {"percentiles": [0.96, 0.98, 0.998, 1.0], "values": [0.0, 0.25, 0.4, 0.45]}


def _key(det: str, col: str) -> str:
    return f"{det}.{col}"


def observation_table(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Long table (steam_id, match_id, feature, x) of every feature observation."""
    parts = []
    for det, col, query in FEATURES:
        df = frames.get(det)
        if df is None or df.empty or "steam_id" not in df:
            continue
        if col == "frac_within_2deg" and col not in df and {"ms_within_2deg", "duration_ms"} <= set(df):
            df = df.assign(frac_within_2deg=df["ms_within_2deg"] / df["duration_ms"].clip(lower=1))
        if col not in df:
            continue
        if query:
            df = df.query(query)
        parts.append(pd.DataFrame({
            "steam_id": df["steam_id"].astype("int64").to_numpy(),
            "match_id": df["match_id"].astype(str).to_numpy() if "match_id" in df else "",
            "feature": _key(det, col),
            "x": pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float),
        }))
    for det, col, _edges in PLAYER_FEATURES:
        df = frames.get(det)
        if df is None or df.empty or "steam_id" not in df:
            continue
        if col == "lattice_fit" and col not in df and "fit_yaw" in df:
            fp = pd.to_numeric(df.get("fit_pitch"), errors="coerce") if "fit_pitch" in df else np.nan
            df = df.assign(lattice_fit=np.fmin(pd.to_numeric(df["fit_yaw"], errors="coerce"), fp))
        if col not in df:
            continue
        parts.append(pd.DataFrame({
            "steam_id": df["steam_id"].astype("int64").to_numpy(),
            "match_id": df["match_id"].astype(str).to_numpy() if "match_id" in df else "",
            "feature": _key(det, col),
            "x": pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float),
        }))
    if not parts:
        return pd.DataFrame(columns=["steam_id", "match_id", "feature", "x"])
    return pd.concat(parts, ignore_index=True).dropna(subset=["x"])


def _fit_tables(clean: pd.DataFrame, cheat: pd.DataFrame, bins: int, clip: float) -> dict:
    tables = {}
    fixed = {_key(d, c): e for d, c, e in PLAYER_FEATURES}
    for feat, c in clean.groupby("feature"):
        h = cheat.loc[cheat["feature"] == feat, "x"].to_numpy()
        c = c["x"].to_numpy()
        need = 50 if feat in fixed else 10 * bins
        if len(c) < need or len(h) < need:
            continue
        edges = np.asarray(fixed[feat]) if feat in fixed else np.unique(np.quantile(c, np.linspace(0, 1, bins + 1)[1:-1]))
        nc = np.bincount(np.searchsorted(edges, c), minlength=len(edges) + 1) + 1.0
        nh = np.bincount(np.searchsorted(edges, h), minlength=len(edges) + 1) + 1.0
        llr = np.clip(np.log((nh / nh.sum()) / (nc / nc.sum())), -clip, clip)
        tables[feat] = {"edges": [round(float(e), 6) for e in edges], "llr": [round(float(v), 4) for v in llr],
                        "n_clean": int(len(c)), "n_cheater": int(len(h)), "per_player": feat in fixed}
    return tables


def _raw_scores(obs: pd.DataFrame, tables: dict, cap: int, min_obs: int, min_features: int) -> pd.DataFrame:
    """Per player: total score and per-feature (n, mean llr, contribution)."""
    rows = []
    for feat, t in tables.items():
        d = obs[obs["feature"] == feat]
        if d.empty:
            continue
        llr = np.asarray(t["llr"])[np.searchsorted(np.asarray(t["edges"]), d["x"].to_numpy())]
        g = pd.DataFrame({"steam_id": d["steam_id"].to_numpy(), "l": llr}).groupby("steam_id")["l"].agg(["mean", "count"])
        pp = bool(t.get("per_player"))
        g = g[g["count"] >= (1 if pp else min_obs)]
        for sid, r in g.iterrows():
            rows.append({"steam_id": int(sid), "feature": feat, "n": int(r["count"]), "mean_llr": float(r["mean"]),
                         "contribution": float(r["mean"] * min(r["count"], 1 if pp else cap)), "per_player": pp})
    per = pd.DataFrame(rows, columns=["steam_id", "feature", "n", "mean_llr", "contribution", "per_player"])
    tot = per.groupby("steam_id").agg(score=("contribution", "sum"),
                                      features=("per_player", lambda x: int((~x.astype(bool)).sum())))
    tot = tot[tot["features"] >= min_features]
    return per[per["steam_id"].isin(tot.index)], tot


def _fold(match_id: str, salt: str = "") -> int:
    # not crc32: its linearity makes a salted split identical to the unsalted one
    return hashlib.sha1((salt + match_id).encode()).digest()[0] % 2


def fit_model(clean: pd.DataFrame, cheat: pd.DataFrame, source: str = "", **params) -> dict:
    """Fit llr tables on ``clean`` / ``cheat`` observation tables.

    The clean reference scores are computed out-of-fold (tables fitted on the
    other half of the matches), so the percentile of a new clean player is not
    biased by having been in the training data.
    """
    p = {**DEFAULTS, **params}
    tables = _fit_tables(clean, cheat, p["bins"], p["clip"])
    ref = []
    for k in (0, 1):
        # salted, so the split stays balanced when the caller already split by _fold
        cf = clean["match_id"].map(lambda m: _fold(m, "ref"))
        hf = cheat["match_id"].map(lambda m: _fold(m, "ref"))
        t = _fit_tables(clean[cf != k], cheat[hf != k], p["bins"], p["clip"])
        _, tot = _raw_scores(clean[cf == k], t, p["cap"], p["min_obs"], p["min_features"])
        ref.extend(tot["score"].tolist())
    return {"source": source, "features": tables, "clean_scores": sorted(round(float(s), 4) for s in ref),
            "clean_matches": int(clean["match_id"].nunique()), "clean_players": len(ref),
            "cheater_players": int(cheat["steam_id"].nunique()), "strength": DEFAULT_STRENGTH,
            **{k: p[k] for k in DEFAULTS}}


def load_model(path: str | Path | None = None, map_name: str | None = None) -> dict | None:
    """Load a model file; ``None``/empty uses the bundled CS2CD model for ``map_name``.

    A map with enough clean dataset players has its own bundled model
    (``player_evidence_cs2cd_<map>.json``); every other map uses the model pooled over all
    dataset maps. A configured ``path`` may contain ``{map}``; when that file does not exist
    the bundled model is used.
    """
    return _load_json(path, map_name, "player_evidence_cs2cd_{map}.json", DEFAULT_MODEL)


def _load_json(path, map_name, per_map: str, default: str) -> dict | None:
    try:
        if path:
            p = Path(str(path).replace("{map}", map_name or "unknown"))
            if p.is_file() or "{map}" not in str(path):
                return json.loads(p.read_text())
        data = resources.files("cs2_analyzer").joinpath("data")
        name = per_map.format(map=map_name) if map_name else None
        if not (name and data.joinpath(name).is_file()):
            name = default
        return json.loads(data.joinpath(name).read_text())
    except (OSError, ValueError):
        return None


def strength_for(percentile: float, model: dict) -> float:
    s = model.get("strength", DEFAULT_STRENGTH)
    return float(np.interp(percentile, s["percentiles"], s["values"]))


def score_players(frames: dict[str, pd.DataFrame], model: dict) -> dict[int, dict]:
    """Per player: score, percentile among clean players, strength and the per-feature breakdown."""
    return score_players_table(observation_table(frames), model)


def score_players_table(obs: pd.DataFrame, model: dict) -> dict[int, dict]:
    per, tot = _raw_scores(obs, model["features"], int(model["cap"]), int(model["min_obs"]),
                           int(model["min_features"]))
    ref = np.asarray(model["clean_scores"], dtype=float)
    out = {}
    for sid, r in tot.iterrows():
        pct = float(np.searchsorted(ref, r["score"], side="right") / len(ref)) if len(ref) else float("nan")
        feats = per[per["steam_id"] == sid].sort_values("contribution", ascending=False)
        out[int(sid)] = {
            "score": round(float(r["score"]), 3),
            "clean_percentile": round(pct, 4),
            "strength": round(strength_for(pct, model), 4),
            "features": {f.feature: {"n": int(f.n), "mean_llr": round(f.mean_llr, 3),
                                     "contribution": round(f.contribution, 3)} for f in feats.itertuples()},
        }
    return out


def _ranges(edges: np.ndarray, mask: np.ndarray) -> list[list[float | None]]:
    """Bins where ``mask`` is set, merged into [low, high] value ranges (None = open end)."""
    out: list[list[float | None]] = []
    for i, on in enumerate(mask):
        if not on:
            continue
        lo = None if i == 0 else float(edges[i - 1])
        hi = None if i == len(edges) else float(edges[i])
        if out and out[-1][1] == lo:
            out[-1][1] = hi
        else:
            out.append([lo, hi])
    return out


def breakdown(obs: pd.DataFrame, model: dict) -> list[dict]:
    """Per feature: the player's typical value next to the clean reference, for the website.

    ``obs`` is :func:`observation_table` for one player. For every feature the player has enough
    observations of: their median, how many observations fell in the value ranges where labelled
    cheaters are more common than clean players (llr > 0) and the same share for clean players,
    and the feature's contribution to the pattern score. Features binned on clean deciles also get
    the clean 10th/50th/90th percentile and where the player's median sits among clean observations.
    Sorted by contribution, strongest first.
    """
    cap, min_obs, bins = int(model["cap"]), int(model["min_obs"]), int(model.get("bins", 10))
    rows = []
    for feat, t in model["features"].items():
        x = obs.loc[obs["feature"] == feat, "x"].to_numpy(dtype=float)
        pp = bool(t.get("per_player"))
        if len(x) < (1 if pp else min_obs):
            continue
        edges, llr = np.asarray(t["edges"], dtype=float), np.asarray(t["llr"], dtype=float)
        idx = np.searchsorted(edges, x)
        cheat = llr > 0
        med = float(np.median(x))
        row = {"feature": feat, "n": int(len(x)), "value": round(med, 4),
               "contribution": round(float(llr[idx].mean() * min(len(x), 1 if pp else cap)), 3),
               "cheaterRanges": _ranges(edges, cheat), "cheaterShare": round(float(cheat[idx].mean()), 3),
               "cleanShare": None, "cleanP10": None, "cleanMedian": None, "cleanP90": None, "cleanPercentile": None}
        if not pp and len(edges) == bins - 1:  # clean deciles: every bin holds 1/bins of clean observations
            q = np.arange(1, bins) / bins
            row |= {"cleanShare": round(float(cheat.mean()), 3), "cleanP10": round(float(edges[0]), 4),
                    "cleanMedian": round(float(edges[len(edges) // 2]), 4), "cleanP90": round(float(edges[-1]), 4),
                    # clamped to 10..90%: outside the deciles the exact position is not known
                    "cleanPercentile": round(float(np.interp(med, edges, q)), 3)}
        rows.append(row)
    return sorted(rows, key=lambda r: -r["contribution"])


def load_observations(obs_dir: str | Path, match_ids: list[str], steam_id: int) -> pd.DataFrame:
    """:func:`observation_table` of one player from the per-match observation files the pipeline writes."""
    frames: dict[str, list[pd.DataFrame]] = {}
    root = Path(obs_dir)
    for mid in match_ids:
        d = root / "".join(c if c.isalnum() or c in "-_." else "_" for c in mid)
        for det in dict.fromkeys(det for det, *_ in FEATURES + PLAYER_FEATURES):  # one read per detector
            f = d / f"{det}.parquet"
            if not f.is_file():
                continue
            df = pd.read_parquet(f)
            if "steam_id" in df:
                frames.setdefault(det, []).append(df[df["steam_id"].astype("int64") == steam_id])
    return observation_table({k: pd.concat(v, ignore_index=True) for k, v in frames.items()})
