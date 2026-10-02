"""Snap-and-return / pinned pitch detector and the per-player evidence score."""

import numpy as np
import pandas as pd

from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.scoring import player_evidence as pe
from cs2_analyzer.scoring.aggregate import assess_match
from cs2_analyzer.testing.synthetic import Scenario, SynthPlayer, static

OBS, ENEMY = 1001, 2001


def scenario(pitch, yaw, shots, moving=False, seconds=30):
    sc = Scenario(seconds=seconds)
    T = sc.T
    pos = static(T, (0, 0, 0))
    if moving:
        pos[:, 0] = np.arange(T) * 200 / sc.tickrate  # 200 u/s
    sc.add(SynthPlayer(OBS, 3, "observer", pos, pitch, yaw))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", static(T, (800, 0, 0)), np.zeros(T), np.full(T, 180.0)))
    for t in shots:
        sc.add_event("shots", steam_id=OBS, tick=int(t) + 1, weapon="ak47")
    return sc


def run(sc, config):
    _, _, _, _, _, _, ctx, events = build_analysis(sc.to_demo(), config, detector_names=["view_integrity"],
                                                   geometry=sc.geometry())
    return [e for e in events if e.steam_id == OBS], ctx


def test_snap_and_return_on_firing_tick_is_flagged(config):
    sc = Scenario()
    T = sc.T
    yaw, pitch = np.zeros(T), np.zeros(T)
    shots = [sc.s(s) for s in (5, 10, 15, 20)]
    for t in shots:
        yaw[t] = 25.0  # view on the target for the firing tick only
    events, ctx = run(scenario(pitch, yaw, shots), config)
    assert len(events) == 1 and events[0].metrics["snap_returns"] == 4
    obs = ctx.observations.frame("view_integrity")
    assert obs["snap_return"].sum() == 4


def test_human_flick_is_not_a_snap_return(config):
    sc = Scenario()
    T = sc.T
    pitch, yaw = np.zeros(T), np.zeros(T)
    shots = []
    for s in (5, 10, 15, 20):
        t = sc.s(s)
        yaw[t - 6:t + 1] = np.linspace(0, 25, 7)  # flick over several ticks, then stay
        yaw[t + 1:t + 40] = 25.0
        yaw[t + 40:t + 50] = np.linspace(25, 0, 10)
        shots.append(t)
    events, ctx = run(scenario(pitch, yaw, shots), config)
    assert events == []
    assert not ctx.observations.frame("view_integrity")["snap_return"].any()


def test_pinned_pitch_while_moving_is_flagged(config):
    sc = Scenario()
    T = sc.T
    events, ctx = run(scenario(np.full(T, 89.0), np.zeros(T), [], moving=True), config)
    assert len(events) == 1 and events[0].metrics["pinned_pitch_fraction"] > 0.9
    # the same pitch while standing still (e.g. AFK) is not evidence
    events, _ = run(scenario(np.full(T, 89.0), np.zeros(T), [], moving=False), config)
    assert events == []


def table(n_players, shift, match_prefix, rng):
    rows = []
    for p in range(n_players):
        for det, col, _ in pe.FEATURES:
            for x in rng.normal(shift, 1.0, 40):
                rows.append({"steam_id": p, "match_id": f"{match_prefix}{p % 20}", "feature": f"{det}.{col}", "x": x})
    return pd.DataFrame(rows)


def test_fit_and_score_separate_shifted_players():
    rng = np.random.default_rng(0)
    clean, cheat = table(200, 0.0, "c", rng), table(200, 0.8, "h", rng)
    model = pe.fit_model(clean, cheat)
    assert set(model["features"]) == {f"{d}.{c}" for d, c, _ in pe.FEATURES}
    assert len(model["clean_scores"]) > 150 and model["clean_scores"] == sorted(model["clean_scores"])
    new = pd.concat([table(1, 0.0, "x", rng), table(1, 2.0, "y", rng).assign(steam_id=7)])
    res = pe.score_players_table(new, model)
    assert res[7]["clean_percentile"] == 1.0 and res[7]["strength"] == 0.45  # capped below HIGH
    assert res[0]["clean_percentile"] < 0.95 and res[0]["strength"] == 0.0
    assert list(res[7]["features"].values())[0]["n"] == 40


def test_profile_joins_score_but_not_corroboration(config):
    cfg = config.section("scoring")
    profile = {"score": 20.0, "clean_percentile": 0.999, "strength": 0.4, "features": {}}
    a = assess_match(1, "m", [], 40, 10, cfg, profile=profile)
    assert a.family_scores["profile"] == 0.4 and abs(a.overall - 0.4) < 1e-9
    assert a.classification == "ELEVATED" and a.to_dict()["player_evidence"] == profile
    assert not any("corroboration" in n for n in a.notes)


def test_bundled_model_loads():
    m = pe.load_model()
    assert m is not None and len(m["features"]) == len(pe.FEATURES) + len(pe.PLAYER_FEATURES)
    assert m["clean_players"] > 300 and m["clean_scores"] == sorted(m["clean_scores"])


def _rows(pcts, days_ago=None):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    days_ago = days_ago or [len(pcts) - i for i in range(len(pcts))]
    return [{"match_id": f"m{i}", "played_at": now - timedelta(days=d), "overall": 0.0, "classification": "NORMAL",
             "profile": {"clean_percentile": p, "strength": 0.0}} for i, (p, d) in enumerate(zip(pcts, days_ago))]


def test_profile_history_needs_consistency_not_volume():
    from cs2_analyzer.scoring.aggregate import profile_history

    assert profile_history(_rows([0.999]), {}) is None  # one match: the match score already says it
    mild = profile_history(_rows([0.9] * 30), {})
    assert mild["strength"] < 0.1  # many mildly unusual matches do not add up to ELEVATED
    high = profile_history(_rows([0.99] * 6), {})
    assert high["strength"] >= 0.25 and len(high["per_match"]) == 6
    # one extreme match among ordinary ones is capped
    spike = profile_history(_rows([0.5] * 5 + [1.0]), {})
    assert spike["strength"] == 0.0 and spike["per_match"][-1]["z"] == 3.0
    assert spike["sudden_change"]


def test_profile_history_decays_old_matches():
    from cs2_analyzer.scoring.aggregate import profile_history

    old_high = profile_history(_rows([0.995] * 3 + [0.5] * 3, days_ago=[900, 900, 900, 3, 2, 1]), {})
    recent_high = profile_history(_rows([0.5] * 3 + [0.995] * 3, days_ago=[900, 900, 900, 3, 2, 1]), {})
    assert recent_high["z"] > old_high["z"]


def test_history_does_not_count_the_profile_twice():
    from cs2_analyzer.scoring.aggregate import assess_history

    cfg = {"min_total_encounters": 10}
    rows = [{**r, "overall": 0.25, "aim_score": 0, "hidden_information_score": 0, "shot_timing_score": 0,
             "recoil_score": 0, "decision_information_score": 0, "encounters_analyzed": 40,
             "profile": {"clean_percentile": 0.98, "strength": 0.25}} for r in _rows([0.98] * 4)]
    h = assess_history(rows, cfg)
    # event evidence is 0 once the profile is taken out; the profile history alone decides
    assert abs(h["historical_evidence_score"] - h["player_evidence_history"]["strength"]) < 1e-9


def test_per_player_feature_counts_once():
    rng = np.random.default_rng(1)
    clean, cheat = table(120, 0.0, "c", rng), table(120, 0.5, "h", rng)
    feat = "input_lattice_summary.lattice_fit"
    clean = pd.concat([clean, pd.DataFrame({"steam_id": range(120), "match_id": [f"c{p % 20}" for p in range(120)],
                                            "feature": feat, "x": 1.0})])
    cheat = pd.concat([cheat, pd.DataFrame({"steam_id": range(120), "match_id": [f"h{p % 20}" for p in range(120)],
                                            "feature": feat, "x": [0.5] * 30 + [1.0] * 90})])
    model = pe.fit_model(clean, cheat)
    t = model["features"][feat]
    assert t["per_player"] and t["edges"] == [0.8, 0.95] and t["llr"][0] > 1.0
    new = pd.concat([table(1, 0.0, "x", rng), pd.DataFrame({"steam_id": [0], "match_id": ["x"], "feature": feat, "x": [0.5]})])
    r = pe.score_players_table(new, model)[0]["features"][feat]
    assert r["n"] == 1 and r["contribution"] == r["mean_llr"] == t["llr"][0]
    frames = {"input_lattice_summary": pd.DataFrame({"steam_id": [5], "fit_yaw": [0.99], "fit_pitch": [0.7]})}
    assert pe.observation_table(frames)["x"].tolist() == [0.7]


def test_pattern_breakdown_endpoint(tmp_path, config):
    """/players/{id}/pattern: the player's value per measurement next to the clean reference."""
    from datetime import datetime, timezone

    import pandas as pd
    from fastapi.testclient import TestClient

    from cs2_analyzer.api.app import create_app
    from cs2_analyzer.storage import models as M
    from cs2_analyzer.storage.repository import Database

    sid, other = 76561198000000042, 76561198000000043
    obs = tmp_path / "obs" / "mX"
    obs.mkdir(parents=True)
    pd.DataFrame({"steam_id": [sid] * 20 + [other] * 5, "trigger_ms": [100.0] * 20 + [10.0] * 5}).to_parquet(
        obs / "trigger_timing.parquet")
    pd.DataFrame({"steam_id": [sid] * 10, "mean_error_deg": [10.0] * 10, "frac_within_2deg": [0.5] * 10}).to_parquet(
        obs / "hidden_tracking.parquet")
    url = f"sqlite:///{tmp_path}/p.sqlite"
    db = Database(url)
    db.init_schema()
    with db.session() as s:
        s.add(M.Player(steam_id=sid, matches_analyzed=1))
        s.add(M.Match(match_id="mX", map="de_mirage", processing_status="COMPLETED",
                      played_at=datetime(2026, 9, 29, tzinfo=timezone.utc)))
        s.flush()
        s.add(M.PlayerMatchAssessment(steam_id=sid, match_id="mX", classification="ELEVATED", evidence_event_count=0,
                                      details={"player_evidence": {"clean_percentile": 0.991, "strength": 0.33}}))
    cfg = config.with_overrides({"output": {"observations_dir": str(tmp_path / "obs")}})
    r = TestClient(create_app(cfg, db_url=url)).get(f"/players/{sid}/pattern").json()
    assert r["matches"][0]["cleanPercentile"] == 0.991 and r["reference"]["cleanPlayers"] > 0
    f = {x["feature"]: x for x in r["features"]}
    t = f["trigger_timing.trigger_ms"]
    assert t["n"] == 20 and t["value"] == 100.0  # the other player's shots are not mixed in
    assert t["cheaterShare"] == 1.0 and 0.3 <= t["cleanShare"] <= 0.5  # 100 ms lies in the cheater-typical range
    assert 0.5 < t["cleanPercentile"] < 0.7 and t["cleanMedian"] == 78.125
    assert f["hidden_tracking.mean_error_deg"]["n"] == 10  # two features of one detector: read once
    assert f["hidden_tracking.frac_within_2deg"]["cleanShare"] is None  # not binned on clean deciles
    assert r["features"][0]["contribution"] >= r["features"][-1]["contribution"]


def test_model_per_map_with_pooled_fallback(tmp_path):
    pooled = pe.load_model()
    assert pooled is not None and pooled.get("map") is None
    assert pe.load_model(map_name="de_nonexistent") == pooled
    for m in ("de_mirage", "de_dust2"):
        own = pe.load_model(map_name=m)
        assert own is not None and own.get("map") in (m, None)
    # a configured path with {map} falls back to the bundled model when the file is missing
    (tmp_path / "x_de_dust2.json").write_text('{"features": {}, "clean_scores": [1.0]}')
    assert pe.load_model(str(tmp_path / "x_{map}.json"), "de_dust2")["clean_scores"] == [1.0]
    assert pe.load_model(str(tmp_path / "x_{map}.json"), "de_nuke") == pe.load_model(map_name="de_nuke")
