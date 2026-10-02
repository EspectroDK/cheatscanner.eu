"""Analyse the CS2CD matches of several maps and export one compact observation table.

For each map with a collision mesh in ``--maps-dir``, every dataset match is
downloaded, analysed (``--no-db``, observations only) and its tick file deleted
again, so disk use stays at a few matches at a time. The run is resumable: a
match whose observations already exist is skipped. At the end the observations
of the play-pattern features (scoring/player_evidence.py) and the population
metrics (scoring/population.py) are written as one long table::

    map, split, match_id, steam_id, cheater, feature, x

which is all ``tools/calibration/cs2cd_per_map.py`` needs, so it can be sent
somewhere else for fitting (tens of MB instead of tens of GB).

    python tools/calibration/cs2cd_maps_run.py --jobs 6
    python tools/calibration/cs2cd_maps_run.py --maps de_dust2 de_inferno --jobs 6

Works the same on Windows and Linux (no shell needed).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd


def _fetch(url: str, out: Path) -> None:
    if out.exists() and out.stat().st_size:
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    for delay in (5, 30, 120, None):
        try:
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(out)
            return
        except OSError:
            if delay is None:
                raise
            time.sleep(delay)


def _one(row: dict, dest: str, obs: str, maps_dir: str) -> tuple[str, str, float]:
    """Download, analyse and delete one match. Returns (match_id, status, seconds)."""
    from cs2_analyzer.parser import cs2cd_backend as cs2cd

    t0 = time.time()
    mid = f"cs2cd-{row['split']}-{row['match']}"
    obs_dir = Path(obs) / row["map"] / row["split"]
    if (obs_dir / mid).is_dir() and any((obs_dir / mid).glob("*.parquet")):
        return mid, "skip", 0.0
    base = Path(dest) / row["split"]
    pq, js = base / f"{row['match']}.parquet", base / f"{row['match']}.json"
    try:
        _fetch(f"{cs2cd.HF_RESOLVE}/{row['split']}/{row['match']}.json", js)
        _fetch(f"{cs2cd.HF_RESOLVE}/{row['split']}/{row['match']}.parquet", pq)
        out_dir = Path(obs).parent / "cs2cd_out_tmp" / mid
        r = subprocess.run([sys.executable, "-m", "cs2_analyzer.cli", "--maps-dir", maps_dir,
                            "analyze", str(pq), "--no-db", "--keep-demo", "--output", str(out_dir),
                            "--observations", str(obs_dir)],
                           capture_output=True, text=True)
        shutil.rmtree(out_dir, ignore_errors=True)
        if r.returncode:
            return mid, "FAILED: " + (r.stderr or r.stdout).strip().splitlines()[-1][:300], time.time() - t0
        return mid, "ok", time.time() - t0
    except Exception as exc:  # noqa: BLE001 - one bad match must not stop the run
        return mid, f"FAILED: {exc}", time.time() - t0
    finally:
        pq.unlink(missing_ok=True)  # the labels (.json) stay: they are small


def export(obs: Path, dest: Path, out: Path) -> pd.DataFrame:
    from cs2_analyzer.calibration import load_observations
    from cs2_analyzer.parser import cs2cd_backend as cs2cd
    from cs2_analyzer.scoring import player_evidence as pe
    from cs2_analyzer.scoring import population as pop

    cheaters = {sid for j in dest.glob("with_cheater_present/*.json")
                for sid in cs2cd.match_labels(j)["cheater_steam_ids"]}
    dets = dict.fromkeys(d for d, *_ in pe.FEATURES + pe.PLAYER_FEATURES + pop.METRICS)
    parts = []
    for split_dir in sorted(obs.glob("*/*")):
        if not split_dir.is_dir():
            continue
        frames = {d: load_observations(split_dir, d) for d in dets}
        t = pe.observation_table(frames)
        extra = []
        for det, col, _sign, _q in pop.METRICS:  # population metrics not already in the play-pattern table
            k = f"{det}.{col}"
            df = frames.get(det)
            if k in set(t["feature"]) or df is None or df.empty or col not in df:
                continue
            extra.append(pd.DataFrame({"steam_id": df["steam_id"].astype("int64").to_numpy(),
                                       "match_id": df["match_id"].astype(str).to_numpy(), "feature": k,
                                       "x": pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)}))
        t = pd.concat([t, *extra], ignore_index=True).dropna(subset=["x"])
        t.insert(0, "split", split_dir.name)
        t.insert(0, "map", split_dir.parent.name)
        parts.append(t)
    table = pd.concat(parts, ignore_index=True)
    table["cheater"] = table["steam_id"].isin(cheaters)
    for c in ("map", "split", "match_id", "feature"):
        table[c] = table[c].astype("category")
    table["x"] = table["x"].astype("float32")
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(out, compression="zstd")
    return table


def main(argv=None) -> int:
    from cs2_analyzer.parser import cs2cd_backend as cs2cd

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--maps", nargs="*", help="maps to run (default: every CS2CD map with a mesh in --maps-dir)")
    ap.add_argument("--maps-dir", default="data/maps")
    ap.add_argument("--dest", default="data/cs2cd", help="download directory (tick files are deleted after use)")
    ap.add_argument("--obs", default="data/cs2cd_obs_maps", help="observations, <obs>/<map>/<split>/<match>/")
    ap.add_argument("--out", default="output/cs2cd_obs_table.parquet")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--export-only", action="store_true")
    args = ap.parse_args(argv)

    index = cs2cd.load_index()
    have = {p.stem for p in Path(args.maps_dir).glob("*.tri") if "." not in p.stem}
    maps = args.maps or sorted({r["map"] for r in index} & have)
    missing = [m for m in maps if m not in have]
    if missing:
        print(f"no mesh in {args.maps_dir} for: {', '.join(missing)}", file=sys.stderr)
        return 2
    rows = [r for r in index if r["map"] in maps]
    print(f"{len(rows)} matches on {', '.join(maps)}", flush=True)
    if not args.export_only:
        log = Path(args.obs) / "run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        done = 0
        with ProcessPoolExecutor(args.jobs) as ex, log.open("a") as lf:
            futs = [ex.submit(_one, r, args.dest, args.obs, args.maps_dir) for r in rows]
            for f in as_completed(futs):
                mid, status, s = f.result()
                done += 1
                line = f"[{done}/{len(rows)}] {mid} {status} {s:.0f}s"
                print(line, flush=True)
                lf.write(line + "\n")
                lf.flush()
    t = export(Path(args.obs), Path(args.dest), Path(args.out))
    size = Path(args.out).stat().st_size / 1e6
    print(f"wrote {args.out}: {len(t)} observations, {t['match_id'].nunique()} matches, "
          f"{t.groupby('map', observed=True)['match_id'].nunique().to_dict()}, {size:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
