"""Precise shots at hidden enemies, compared with ghost enemies from other rounds (detectors/hidden_fire.py)."""

import numpy as np
import pandas as pd

from cs2_analyzer.parser.normalize import assign_rounds
from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.testing.synthetic import SynthPlayer, aim_at, static, wall_scenario

OBS, ENEMY = 1001, 2001
SPOT = (800.0, 0.0, 0.0)  # behind the wall
OTHER = (800.0, 600.0, 0.0)  # also behind the wall, far off the crosshair


def two_rounds(demo, T):
    """Split the synthetic match into two rounds of equal length."""
    half = T // 2
    tick0 = int(demo.ticks["tick"].min())
    rows = []
    for k, start in enumerate((tick0, tick0 + half)):
        rows.append({"round_number": k + 1, "start_tick": start, "freeze_end_tick": start + 32,
                     "end_tick": start + half - 1, "winner": None, "reason": None, "live": True})
    demo.rounds = pd.DataFrame(rows)
    r, live = assign_rounds(demo.ticks["tick"].to_numpy(), demo.rounds)
    demo.ticks["round"] = r
    demo.ticks["round_live"] = live


def run(config, enemy_round2, shoot_round2, n_hits=12):
    sc = wall_scenario(60)
    T, half = sc.T, sc.T // 2
    observer = static(T, (0, 0, 0))
    eye = observer.copy()
    eye[:, 2] += 64
    enemy = static(T, SPOT)
    enemy[half:] = enemy_round2
    head = enemy.copy()
    head[:, 2] += 64
    pitch, yaw = aim_at(eye, head)  # the crosshair is on him through the wall, wherever he is
    sc.add(SynthPlayer(OBS, 3, "observer", observer, pitch, yaw))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", enemy, np.zeros(T), np.full(T, 180.0), walking=True))
    demo = sc.to_demo()
    two_rounds(demo, T)
    bursts = [sc.s(4 + 3 * i) for i in range(6)]  # one shot per burst, 3 s apart
    shots = [b for b in bursts] + ([half + b for b in bursts] if shoot_round2 else [])
    demo.events["shots"] = pd.DataFrame({"tick": [t + 1 for t in shots], "steam_id": OBS, "weapon": "ak47"})
    hurts = [{"tick": sc.s(4) + 1 + 20 * i, "attacker_steam_id": OBS, "victim_steam_id": ENEMY, "weapon": "ak47",
              "hitgroup": "chest", "dmg_health": 5, "dmg_armor": 0, "health": 95} for i in range(n_hits)]
    demo.events["hurts"] = pd.DataFrame(hurts)
    *_, ctx, events = build_analysis(demo, config, detector_names=["hidden_fire"], geometry=sc.geometry())
    obs = ctx.observations.frame("hidden_fire")
    return [e for e in events if e.steam_id == OBS], obs[obs.steam_id == OBS].iloc[0]


def test_shooting_where_the_hidden_enemy_is_only_when_he_is_there(config):
    """The enemy holds a different spot in each round and every burst finds him: not habit."""
    events, obs = run(config, OTHER, shoot_round2=True)
    assert obs["precise_bursts"] == 12
    assert obs["expected_precise_bursts"] < 1
    assert obs["hidden_hits"] == 12
    assert len(events) == 1 and events[0].context["scope"] == "match"
    assert events[0].metrics["excess"] >= 3


def test_shooting_a_spot_the_enemy_always_holds_is_habit(config):
    """The enemy stands on the same spot in both rounds, so ghosts explain every precise burst."""
    events, obs = run(config, SPOT, shoot_round2=True)
    assert obs["precise_bursts"] == 12
    assert obs["excess"] < 1
    assert events == []


def test_precise_bursts_without_hidden_hits_are_not_enough(config):
    events, obs = run(config, OTHER, shoot_round2=True, n_hits=2)
    assert obs["excess"] >= 3 and obs["hidden_hits"] == 2
    assert events == []
