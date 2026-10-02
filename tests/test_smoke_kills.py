import numpy as np
import pandas as pd

from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.testing.synthetic import SynthPlayer, static, wall_scenario

OBS, ENEMY = 1001, 2001


def _run(config, n_kills, n_smoke):
    sc = wall_scenario(30)
    T = sc.T
    sc.add(SynthPlayer(OBS, 3, "o", static(T, (0, 0, 0)), np.zeros(T), np.zeros(T)))
    sc.add(SynthPlayer(ENEMY, 2, "e", static(T, (800, 0, 0)), np.zeros(T), np.full(T, 180.0)))
    demo = sc.to_demo()
    rows = [{"tick": 100 + 50 * i, "attacker_steam_id": OBS, "victim_steam_id": ENEMY, "assister_steam_id": 0,
             "weapon": "ak47", "headshot": False, "penetrated": False, "thrusmoke": i < n_smoke,
             "attackerblind": False, "noscope": False, "hitgroup": "chest"} for i in range(n_kills)]
    demo.events["deaths"] = pd.DataFrame(rows)
    *_, ctx, events = build_analysis(demo, config, detector_names=["smoke_kills"], geometry=sc.geometry())
    return [e for e in events if e.detector_type == "smoke_kills"], ctx


def test_many_kills_through_smoke_is_evidence(config):
    events, ctx = _run(config, 26, 13)
    assert len(events) == 1 and events[0].steam_id == OBS
    assert events[0].context["scope"] == "match" and events[0].metrics["smoke_kills"] == 13
    obs = ctx.observations.frame("smoke_kills")
    assert obs.loc[obs.steam_id == OBS, "smoke_kill_share"].iloc[0] == 0.5


def test_few_or_diluted_smoke_kills_are_not(config):
    assert _run(config, 26, 4)[0] == []  # a few lucky sprays
    assert _run(config, 40, 10)[0] == []  # 10 of 40 kills: 25% share
