"""Per-map play-pattern models and population references from the CS2CD observation table.

Input: one or more tables written by ``cs2cd_maps_run.py`` (map, split, match_id, steam_id,
cheater, feature, x). For each map it compares, on held-out matches (two folds by match):

* ``before``  the model bundled before per-map references (fitted on Mirage only)
* ``pooled``  one model fitted on the clean players and labelled cheaters of every map
* ``per-map`` llr tables and clean reference scores fitted on that map alone
* ``hybrid``  llr tables from every map, clean reference scores from that map

and reports, per map and group, how often the play-pattern strength reaches ELEVATED (clean 98th
percentile) and the AUC of the pattern score. With ``--write`` it writes the models that ship:
the pooled model, and one per map with at least ``--min-clean-players`` clean reference players
(``--mode`` picks per-map or hybrid). Population references (scoring/population.py) are written
the same way.

    python tools/calibration/cs2cd_per_map.py output/cs2cd_obs_mirage.parquet output/cs2cd_obs_table.parquet \\
        --exclude docs/validation/cs2cd/anti_aim_clean_players.txt --out docs/validation/cs2cd/per_map.md --write
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

from cs2_analyzer.scoring import player_evidence as pe
from cs2_analyzer.scoring import population as pop

DATA = Path(__file__).resolve().parents[2] / "src" / "cs2_analyzer" / "data"
ELEVATED = 0.25


def load(paths: list[str], exclude: set[int]) -> pd.DataFrame:
    t = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    for c in ("map", "split", "match_id", "feature"):
        t[c] = t[c].astype(str)
    t = t[~t["steam_id"].isin(exclude)]
    t["group"] = np.where(t["cheater"], "cheater", np.where(t["split"] == "no_cheater_present", "clean", "unlabelled"))
    return t


def pe_obs(t: pd.DataFrame) -> pd.DataFrame:
    feats = {pe._key(d, c) for d, c, *_ in pe.FEATURES + pe.PLAYER_FEATURES}
    return t[t["feature"].isin(feats)]


def fit(t: pd.DataFrame, source: str) -> dict:
    t = pe_obs(t)
    cols = ["steam_id", "match_id", "feature", "x"]
    return pe.fit_model(t.loc[t["group"] == "clean", cols], t.loc[t["group"] == "cheater", cols], source=source)


def hybrid(t: pd.DataFrame, m: str, source: str) -> dict:
    """Pooled llr tables; clean reference scores from map ``m``'s clean players, scored out-of-fold."""
    pooled = fit(t, source)
    obs = pe_obs(t)
    scores = []
    for k in (0, 1):
        fold = obs["match_id"].map(lambda x: pe._fold(x, "ref"))
        train = obs[fold != k]
        tabs = pe._fit_tables(train.loc[train["group"] == "clean"], train.loc[train["group"] == "cheater"],
                              pooled["bins"], pooled["clip"])
        test = obs[(fold == k) & (obs["map"] == m) & (obs["group"] == "clean")]
        _, tot = pe._raw_scores(test, tabs, pooled["cap"], pooled["min_obs"], pooled["min_features"])
        scores.extend(tot["score"].tolist())
    return {**pooled, "source": source, "clean_scores": sorted(round(float(s), 4) for s in scores),
            "clean_players": len(scores), "clean_matches": int(obs.loc[(obs["map"] == m) & (obs["group"] == "clean"),
                                                                        "match_id"].nunique())}


def score(model: dict, t: pd.DataFrame) -> pd.DataFrame:
    res = pe.score_players_table(pe_obs(t)[["steam_id", "match_id", "feature", "x"]], model)
    return pd.DataFrame([{"steam_id": s, "score": r["score"], "pct": r["clean_percentile"], "strength": r["strength"]}
                         for s, r in res.items()])


def auc(pos, neg) -> float | None:
    pos, neg = np.asarray(pos), np.asarray(neg)
    if len(pos) < 5 or len(neg) < 5:
        return None
    return float(mannwhitneyu(pos, neg).statistic / (len(pos) * len(neg)))


def evaluate(t: pd.DataFrame, before: dict | None) -> pd.DataFrame:
    """Held-out scores per player and option: fit on one half of the matches, score the other."""
    groups = t.groupby("steam_id")[["map", "group"]].first()
    fold = t["match_id"].map(lambda x: pe._fold(x, "eval"))
    rows = []
    for k in (0, 1):
        train, test = t[fold != k], t[fold == k]
        pooled = fit(train, "pooled")
        for m, test_m in test.groupby("map"):
            options = {"pooled": pooled}
            if before is not None:
                options["before"] = before
            train_m = train[train["map"] == m]
            if train_m["group"].eq("clean").any() and train_m["group"].eq("cheater").any():
                options["per-map"] = fit(train_m, m)
            if train_m["group"].eq("clean").any():
                options["hybrid"] = hybrid(train, m, m)
            for name, model in options.items():
                if not model["clean_scores"] or not model["features"]:
                    continue
                s = score(model, test_m)
                if s.empty:
                    continue
                s["option"] = name
                rows.append(s)
    out = pd.concat(rows, ignore_index=True)
    return out.join(groups, on="steam_id")


def report(ev: pd.DataFrame, t: pd.DataFrame, before_is_in_sample: bool) -> str:
    lines = ["| map | clean matches | clean players | cheaters | option | clean ≥ ELEVATED | unlabelled ≥ ELEVATED "
             "| cheaters ≥ ELEVATED | AUC cheater vs clean |", "|---|---|---|---|---|---|---|---|---|"]
    counts = t.groupby(["map", "group"]).agg(matches=("match_id", "nunique"), players=("steam_id", "nunique"))
    order = ["before", "pooled", "per-map", "hybrid"]
    for m in sorted(ev["map"].unique()) + ["all maps"]:
        e = ev if m == "all maps" else ev[ev["map"] == m]
        if m == "all maps":
            cm, cp, ch = (counts.xs(g, level="group").sum()[c] if g in counts.index.get_level_values("group") else 0
                          for g, c in (("clean", "matches"), ("clean", "players"), ("cheater", "players")))
        else:
            c = counts.loc[m]
            cm = c.loc["clean", "matches"] if "clean" in c.index else 0
            cp = c.loc["clean", "players"] if "clean" in c.index else 0
            ch = c.loc["cheater", "players"] if "cheater" in c.index else 0
        for o in order:
            eo = e[e["option"] == o]
            if eo.empty:
                continue
            rate = {}
            for g in ("clean", "unlabelled", "cheater"):
                x = eo.loc[eo["group"] == g, "strength"]
                rate[g] = f"{(x >= ELEVATED).mean() * 100:.1f}% ({(x >= ELEVATED).sum()}/{len(x)})" if len(x) else "–"
            a = auc(eo.loc[eo["group"] == "cheater", "score"], eo.loc[eo["group"] == "clean", "score"])
            label = o + (" *" if o == "before" and m in ("de_mirage", "all maps") and before_is_in_sample else "")
            lines.append(f"| {m} | {cm} | {cp} | {ch} | {label} | {rate['clean']} | {rate['unlabelled']} "
                         f"| {rate['cheater']} | {'–' if a is None else f'{a:.2f}'} |")
    return "\n".join(lines)


def population_refs(t: pd.DataFrame) -> dict[str, dict]:
    """Population references per map and pooled, from the clean players' per-player medians."""
    keys = {pop._key(d, c): (d, c, s) for d, c, s, _q in pop.METRICS}
    clean = t[(t["group"] == "clean") & t["feature"].isin(keys)]
    g = clean.groupby(["map", "steam_id", "feature"])["x"].agg(["median", "count"]).reset_index()
    g = g[g["count"] >= 5]
    out = {}
    for m, gm in list(g.groupby("map")) + [("all", g)]:
        ref = {"source": f"CS2CD clean matches, {m}", "matches": int(clean.loc[clean["map"] == m, "match_id"].nunique()
                                                                     if m != "all" else clean["match_id"].nunique()),
               "min_obs": 5, "map": None if m == "all" else m, "metrics": {}}
        for k, (_d, _c, sign) in keys.items():
            v = np.sort(gm.loc[gm["feature"] == k, "median"].to_numpy(dtype=float))
            if len(v):
                ref["metrics"][k] = {"sign": sign, "n_players": int(len(v)), "values": [round(float(x), 5) for x in v]}
        out[m] = ref
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tables", nargs="+")
    ap.add_argument("--exclude", help="SteamIDs left out of the clean reference (e.g. rage cheaters)")
    ap.add_argument("--before", default="",
                    help="model to report as 'before' (e.g. the Mirage-only model from git history)")
    ap.add_argument("--min-clean-players", type=int, default=250)
    ap.add_argument("--mode", choices=["hybrid", "per-map"], default="hybrid")
    ap.add_argument("--out", default="output/cs2cd_per_map.md")
    ap.add_argument("--write", action="store_true", help="write the bundled models/references to --data-dir")
    ap.add_argument("--data-dir", default=str(DATA))
    args = ap.parse_args(argv)

    exclude = {int(x) for x in Path(args.exclude).read_text().split()} if args.exclude else set()
    t = load(args.tables, exclude)
    before = json.loads(Path(args.before).read_text()) if args.before and Path(args.before).is_file() else None
    ev = evaluate(t, before)
    md = ["# Play-pattern reference per map (CS2CD)", "",
          "Held-out: every model is fitted on one half of the matches and scores the other half. "
          "`* before` on Mirage is in-sample (it was fitted on these Mirage matches), so it looks better than it is.",
          "", report(ev, t, before_is_in_sample=True), ""]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(md))
    print("\n".join(md))
    ev.to_csv(Path(args.out).with_suffix(".csv"), index=False)

    if args.write:
        d = Path(args.data_dir)
        maps = sorted(t["map"].unique())
        pooled = fit(t, f"CS2CD, {len(maps)} maps: " + ", ".join(maps))
        (d / "player_evidence_cs2cd_all.json").write_text(json.dumps(pooled))
        print(f"pooled: {pooled['clean_players']} clean reference players, {pooled['cheater_players']} cheaters")
        refs = population_refs(t)
        (d / "population_reference_cs2cd_all.json").write_text(json.dumps(refs["all"]))
        for m in maps:
            tm = t[t["map"] == m]
            n_clean = tm.loc[tm["group"] == "clean", "steam_id"].nunique()
            if n_clean < args.min_clean_players:
                print(f"{m}: {n_clean} clean players, uses the pooled model")
                continue
            model = hybrid(t, m, f"CS2CD {m} (clean reference) + all maps (llr tables)") if args.mode == "hybrid" \
                else fit(tm, f"CS2CD {m}")
            model["map"] = m
            (d / f"player_evidence_cs2cd_{m}.json").write_text(json.dumps(model))
            (d / f"population_reference_cs2cd_{m}.json").write_text(json.dumps(refs[m]))
            print(f"{m}: {model['clean_players']} clean reference players")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
