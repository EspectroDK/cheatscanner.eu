"""Compare detector output for CS2CD players: clean matches vs labelled cheaters.

Usage (after analysing dataset matches with ``--observations <obs>/<split>``)::

    python tools/calibration/cs2cd_compare.py --dataset data/cs2cd --obs data/cs2cd_obs \
        --output output --out output/cs2cd_compare.md

Groups:

* ``clean``      players in ``no_cheater_present`` matches (legitimate baseline;
                 the dataset authors found 97.2% of a sample showed no cheating)
* ``unlabelled`` players in ``with_cheater_present`` matches not labelled cheater
                 (the authors measured only 55.6% precision for this "not cheater"
                 label, so the group contains undetected cheaters)
* ``cheater``    players labelled as VAC-banned cheaters. Bans are account-level
                 and were not necessarily for this match; treat separations as a
                 lower bound.

AUC is P(value of a random labelled cheater > value of a random clean player);
0.5 means the metric does not separate the groups, values far below 0.5 separate
in the other direction. ``AUC same-match`` compares labelled cheaters with the
unlabelled players of the same matches, which controls for rank and server.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

from cs2_analyzer.calibration import REPORT_METRICS, load_observations
from cs2_analyzer.parser.cs2cd_backend import match_labels

GROUPS = ["clean", "unlabelled", "cheater"]
CLASSES = ["INSUFFICIENT_DATA", "NORMAL", "ELEVATED", "HIGH", "VERY_HIGH"]


def auc(pos: pd.Series, neg: pd.Series) -> float | None:
    pos, neg = pos.dropna(), neg.dropna()
    if len(pos) < 5 or len(neg) < 5:
        return None
    return float(mannwhitneyu(pos, neg).statistic / (len(pos) * len(neg)))


def player_groups(dataset: Path) -> dict[int, str]:
    return {sid: "cheater" for j in dataset.glob("with_cheater_present/*.json")
            for sid in match_labels(j)["cheater_steam_ids"]}


def group_of(sid: int, cheaters: dict[int, str]) -> str:
    if sid in cheaters:
        return "cheater"
    split = (int(sid) - 900_000_000_000) // 10_000_000
    return "clean" if split == 1 else "unlabelled"


def assessments(output: Path, cheaters: dict[int, str]) -> pd.DataFrame:
    rows = []
    for mj in output.glob("cs2cd-*/match.json"):
        d = json.loads(mj.read_text())
        kills = {int(p["steam_id"]): p for p in d["players"]}
        for sid, a in d["assessments"].items():
            sid = int(sid)
            rows.append({"steam_id": sid, "match_id": a["match_id"], "group": group_of(sid, cheaters),
                         "classification": a["classification"], "overall": a["overall_evidence_score"],
                         # event evidence only: the profile family taken back out (it is scored cross-fitted below)
                         "overall_events": 1 - (1 - a["overall_evidence_score"])
                         / max(1e-9, 1 - (a.get("families") or {}).get("profile", 0.0)),
                         "events": a["evidence_event_count"], "encounters": a["encounters_analyzed"],
                         "kills": kills.get(sid, {}).get("kills"), "deaths": kills.get(sid, {}).get("deaths"),
                         **{f"axis.{k}": v for k, v in a["axes"].items()}})
    return pd.DataFrame(rows)


def population_section(obs_root: Path, cheaters: dict, min_obs: int, extra: dict[str, Path] | None = None) -> list[str]:
    """Combined clean-population percentile (scoring/population.py) by group.

    The reference is the clean split. Clean players are scored against a
    reference rebuilt without their own match (leave-one-match-out), so their
    rate is an out-of-sample false-positive estimate.
    """
    from cs2_analyzer.scoring import population as pop

    clean_dir = obs_root / "no_cheater_present"
    if not clean_dir.is_dir():
        return []
    full = pop.build_reference(clean_dir, min_obs=min_obs)
    rows = []
    for split_dir in sorted(d for d in obs_root.iterdir() if d.is_dir()):
        for mdir in sorted(d for d in split_dir.iterdir() if d.is_dir()):
            frames = {}
            for det, *_ in pop.METRICS:
                f = mdir / f"{det}.parquet"
                frames[det] = pd.read_parquet(f) if f.exists() else pd.DataFrame()
            ref = full
            if split_dir.name == "no_cheater_present":
                ref = _reference_without(clean_dir, mdir.name, min_obs)
            for sid, r in pop.percentiles(frames, ref, min_obs).items():
                rows.append({"steam_id": sid, "group": group_of(sid, cheaters), "pct": r["combined_percentile"]})
    for label, root in (extra or {}).items():
        for mdir in sorted(d for d in Path(root).iterdir() if d.is_dir()):
            frames = {det: pd.read_parquet(mdir / f"{det}.parquet") if (mdir / f"{det}.parquet").exists()
                      else pd.DataFrame() for det, *_ in pop.METRICS}
            for sid, r in pop.percentiles(frames, full, min_obs).items():
                rows.append({"steam_id": sid, "group": label, "pct": r["combined_percentile"]})
    P = pd.DataFrame(rows).dropna()
    groups = GROUPS + list(extra or {})
    if P.empty:
        return []
    L = ["## Combined clean-population percentile", "",
         "Mean oriented percentile over " + ", ".join(f"`{d}.{c}`" for d, c, *_ in pop.METRICS)
         + f" (players with at least 2 of them, {min_obs}+ observations each). Clean players are scored "
         "leave-one-match-out.", "",
         f"AUC labelled cheater vs clean: {auc(P[P.group == 'cheater'].pct, P[P.group == 'clean'].pct):.3f}; "
         f"vs same-match unlabelled: {auc(P[P.group == 'cheater'].pct, P[P.group == 'unlabelled'].pct):.3f}", "",
         "| cutoff | " + " | ".join(f"{g} (n={int((P.group == g).sum())})" for g in groups) + " |",
         "|---" * (1 + len(groups)) + "|"]
    for t in (0.8, 0.85, 0.9, 0.95):
        L.append(f"| >= {t} | " + " | ".join(f"{(P[P.group == g].pct >= t).mean() * 100:.1f}%" for g in groups) + " |")
    L.append("")
    return L


def player_evidence_section(obs_root: Path, cheaters: dict, A: pd.DataFrame, exclude: set[int],
                            extra: dict[str, Path] | None = None) -> list[str]:
    """Per-player evidence (scoring/player_evidence.py), cross-fitted by match.

    Llr tables and the clean reference are fitted on one half of the matches
    and applied to the other half, so every CS2CD player is scored out of
    sample. Extra corpora are scored with a model fitted on all matches. The
    class columns combine the profile strength with each player's existing
    evidence score, as the pipeline now does.
    """
    from cs2_analyzer.calibration import load_observations
    from cs2_analyzer.scoring import player_evidence as pe

    def table(root):
        return pe.observation_table({det: load_observations(root, det) for det, *_ in pe.FEATURES + pe.PLAYER_FEATURES})

    clean_dir, cheat_dir = obs_root / "no_cheater_present", obs_root / "with_cheater_present"
    if not (clean_dir.is_dir() and cheat_dir.is_dir()):
        return []
    clean, mixed = table(clean_dir), table(cheat_dir)
    clean = clean[~clean["steam_id"].isin(exclude)]
    ch = mixed[mixed["steam_id"].isin(set(cheaters))]
    fold = lambda t: t["match_id"].map(pe._fold)  # noqa: E731
    rows = []
    for k in (0, 1):
        model = pe.fit_model(clean[fold(clean) != k], ch[fold(ch) != k])
        test = pd.concat([clean[fold(clean) == k], mixed[fold(mixed) == k]])
        frames_players = pe.score_players_table(test, model)
        rows += [{"steam_id": sid, "group": group_of(sid, cheaters), **r} for sid, r in frames_players.items()]
    full = pe.fit_model(clean, ch)
    for label, root in (extra or {}).items():
        rows += [{"steam_id": sid, "group": label, **r} for sid, r in pe.score_players_table(table(root), full).items()]
    P = pd.DataFrame(rows)
    if P.empty:
        return []
    prev = A.set_index("steam_id")["overall_events"] if len(A) else pd.Series(dtype=float)
    P["overall_before"] = P["steam_id"].map(prev)
    P["overall_after"] = 1 - (1 - P["overall_before"]) * (1 - P["strength"])
    groups = GROUPS + list(extra or {})
    L = ["## Player evidence over the match (cross-fitted)", "",
         "Sum over " + ", ".join(f"`{d}.{c}`" for d, c, _ in pe.FEATURES) + " of the mean log-likelihood ratio "
         f"times min(n, {pe.DEFAULTS['cap']}), plus the ratio of the per-player "
         + ", ".join(f"`{d}.{c}`" for d, c, _ in pe.PLAYER_FEATURES)
         + f". Players excluded from the clean side: {len(exclude)}.", "",
         f"AUC labelled cheater vs clean: {auc(P[P.group == 'cheater'].score, P[P.group == 'clean'].score):.3f}; "
         f"vs same-match unlabelled: {auc(P[P.group == 'cheater'].score, P[P.group == 'unlabelled'].score):.3f}", "",
         "| | " + " | ".join(f"{g} (n={int((P.group == g).sum())})" for g in groups) + " |",
         "|---" * (1 + len(groups)) + "|"]
    for t in (0.95, 0.98, 0.995):
        L.append(f"| above clean p{t * 100:g} | " + " | ".join(
            f"{(P[P.group == g].clean_percentile > t).mean() * 100:.1f}%" for g in groups) + " |")
    for name, lo in (("ELEVATED or above, events only", None), ("ELEVATED or above, with profile", 0.25),
                     ("HIGH or above, with profile", 0.5)):
        col = "overall_before" if lo is None else "overall_after"
        lo = 0.25 if lo is None else lo
        L.append(f"| {name} | " + " | ".join(
            "n/a" if P[P.group == g][col].isna().all() else f"{(P[P.group == g][col] >= lo).mean() * 100:.1f}%"
            for g in groups) + " |")
    L.append("")
    return L


def _reference_without(clean_dir: Path, match: str, min_obs: int) -> dict:
    import tempfile

    from cs2_analyzer.scoring import population as pop

    with tempfile.TemporaryDirectory() as tmp:
        for d in clean_dir.iterdir():
            if d.is_dir() and d.name != match:
                (Path(tmp) / d.name).symlink_to(d.resolve(), target_is_directory=True)
        return pop.build_reference(tmp, min_obs=min_obs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", required=True, help="dataset dir with <split>/<n>.json")
    ap.add_argument("--obs", required=True, help="observations root with one subdir per split")
    ap.add_argument("--output", required=True, help="analysis output dir with cs2cd-*/match.json")
    ap.add_argument("--out", required=True, help="markdown report path")
    ap.add_argument("--extra-clean", action="append", default=[], metavar="LABEL=DIR",
                    help="another clean corpus (observations dir with one subdir per match), e.g. pro=obs/pro")
    ap.add_argument("--exclude-clean", help="file with SteamIDs to leave out of the clean side (e.g. anti-aim players)")
    ap.add_argument("--min-obs", type=int, default=5, help="observations per player for a per-player median")
    args = ap.parse_args(argv)

    cheaters = player_groups(Path(args.dataset))
    A = assessments(Path(args.output), cheaters)
    L = ["# CS2CD: clean players vs labelled cheaters", ""]
    n_matches = A.groupby("group")["match_id"].nunique().to_dict() if len(A) else {}
    L.append("| group | players | matches | " + " | ".join(CLASSES) + " | mean score | events/player |")
    L.append("|---" * (5 + len(CLASSES)) + "|")
    for g in GROUPS:
        s = A[A["group"] == g]
        if s.empty:
            continue
        pct = s["classification"].value_counts(normalize=True)
        L.append(f"| {g} | {len(s)} | {n_matches.get(g, 0)} | "
                 + " | ".join(f"{pct.get(c, 0) * 100:.1f}%" for c in CLASSES)
                 + f" | {s['overall'].mean():.3f} | {s['events'].mean():.2f} |")
    L.append("")
    clean, ch = A[A["group"] == "clean"], A[A["group"] == "cheater"]
    L.append("## Score separation (AUC, labelled cheater vs clean player)")
    L.append("")
    unl = A[A["group"] == "unlabelled"]
    L.append("| score | AUC | AUC same-match | clean p50 / p95 | cheater p50 / p95 |")
    L.append("|---|---|---|---|---|")
    for col in ["overall", "events"] + [c for c in A.columns if c.startswith("axis.")]:
        a = auc(ch[col], clean[col])
        a2 = auc(ch[col], unl[col])
        L.append(f"| {col} | {a:.3f} | {'n/a' if a2 is None else f'{a2:.3f}'} | {clean[col].median():.3g} / {clean[col].quantile(.95):.3g} | "
                 f"{ch[col].median():.3g} / {ch[col].quantile(.95):.3g} |" if a is not None else f"| {col} | n/a | | | |")
    L.append("")

    L.append("## Raw detector metrics, per-player medians")
    L.append("")
    L.append(f"Players with at least {args.min_obs} observations of the metric. AUC > 0.5 means labelled "
             "cheaters have higher values.")
    L.append("")
    L.append("| metric | clean n / p50 / p95 | unlabelled n / p50 | cheater n / p50 / p95 | AUC | AUC same-match |")
    L.append("|---|---|---|---|---|---|")
    obs_root = Path(args.obs)
    for det, col, query, desc in REPORT_METRICS:
        frames = [load_observations(d, det) for d in obs_root.iterdir() if d.is_dir()]
        df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame()
        if df.empty or col not in df or "steam_id" not in df:
            continue
        if query:
            try:
                df = df.query(query)
            except Exception:
                pass
        df[col] = pd.to_numeric(df[col], errors="coerce")
        per = df.groupby("steam_id")[col].agg(["median", "count"])
        per = per[per["count"] >= args.min_obs]
        per["group"] = [group_of(s, cheaters) for s in per.index]
        m = {g: per.loc[per["group"] == g, "median"] for g in GROUPS}
        a = auc(m["cheater"], m["clean"])
        a2 = auc(m["cheater"], m["unlabelled"])

        def q(s, p):
            return f"{s.quantile(p):.4g}" if len(s) else "-"

        L.append(f"| {desc} (`{det}.{col}`) | {len(m['clean'])} / {q(m['clean'], .5)} / {q(m['clean'], .95)} | "
                 f"{len(m['unlabelled'])} / {q(m['unlabelled'], .5)} | "
                 f"{len(m['cheater'])} / {q(m['cheater'], .5)} / {q(m['cheater'], .95)} | "
                 f"{'n/a' if a is None else f'{a:.3f}'} | {'n/a' if a2 is None else f'{a2:.3f}'} |")
    L.append("")
    extra = dict(x.split("=", 1) for x in args.extra_clean)
    L += population_section(obs_root, cheaters, args.min_obs, {k: Path(v) for k, v in extra.items()})
    excl = {int(x) for x in Path(args.exclude_clean).read_text().split()} if args.exclude_clean else set()
    L += player_evidence_section(obs_root, cheaters, A, excl, {k: Path(v) for k, v in extra.items()})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(L))
    A.to_csv(Path(args.out).with_suffix(".players.csv"), index=False)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
