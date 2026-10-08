"""Knife or bomb out only when no hidden enemy is near (detectors/safe_carelessness.py)."""

import numpy as np
import pandas as pd

from cs2_analyzer.parser.normalize import assign_rounds
from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.testing.synthetic import SynthPlayer, static, wall_scenario

OBS, ENEMY = 1001, 2001
NEAR = (600.0, 0.0, 0.0)  # behind the wall, 600 u away
FAR = (2000.0, 0.0, 0.0)  # behind the wall, out of the threat radius
ROUNDS = 8


def split_rounds(demo, T, n):
    tick0 = int(demo.ticks["tick"].min())
    size = T // n
    demo.rounds = pd.DataFrame([{"round_number": k + 1, "start_tick": tick0 + k * size, "freeze_end_tick": tick0 + k * size + 32,
                                 "end_tick": tick0 + (k + 1) * size - 1, "winner": None, "reason": None, "live": True}
                                for k in range(n)])
    r, live = assign_rounds(demo.ticks["tick"].to_numpy(), demo.rounds)
    demo.ticks["round"] = r
    demo.ticks["round_live"] = live


def even_rounds(k, t):
    return k % 2 == 0


def run(config, knife, near=even_rounds):
    """The enemy waits behind the wall 600 u away while ``near(round, tick_in_round)``, else 2,000 u away.
    ``knife(round, tick_in_round, enemy_near)`` says whether the observer holds the knife."""
    sc = wall_scenario(ROUNDS * 30)
    T, size = sc.T, sc.T // ROUNDS
    enemy = np.zeros((T, 3))
    cls = np.empty(T, dtype=object)
    for k in range(ROUNDS):
        for t in range(size):
            n = near(k, t)
            enemy[k * size + t] = NEAR if n else FAR
            cls[k * size + t] = "knife" if knife(k, t, n) else "rifle"
    cls[ROUNDS * size:] = "rifle"
    enemy[ROUNDS * size:] = FAR
    enemy[:, 1] += 2.0 * np.sin(np.arange(T) / 16.0)
    sc.add(SynthPlayer(OBS, 3, "observer", static(T, (0, 0, 0)), np.zeros(T), np.zeros(T), weapon_class=cls))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", enemy, np.zeros(T), np.full(T, 180.0), walking=True))
    demo = sc.to_demo()
    split_rounds(demo, T, ROUNDS)
    *_, ctx, events = build_analysis(demo, config, detector_names=["safe_carelessness"], geometry=sc.geometry())
    obs = ctx.observations.frame("safe_carelessness")
    return [e for e in events if e.steam_id == OBS], obs[obs.steam_id == OBS].iloc[0]


def test_knife_out_only_when_the_hidden_enemy_is_far(config):
    events, obs = run(config, lambda k, t, near: not near)
    assert obs["safe_rate"] == 1.0 and obs["threat_rate"] == 0.0
    assert obs["excess"] > 1.0
    assert len(events) == 1 and events[0].context["scope"] == "match"


def test_same_knife_habit_every_round_does_not_fire(config):
    """Knife out for the first ten seconds of every round, whatever the enemy does."""
    events, obs = run(config, lambda k, t, near: t < 64 * 10)
    assert abs(obs["excess"]) < 0.1
    assert events == []


def test_too_few_moments_near_a_hidden_enemy_are_left_alone(config):
    """The enemy is close for only the last 8 s of one round: too few threat moments to judge, even though
    the knife is away exactly then."""
    size = 30 * 64
    events, obs = run(config, lambda k, t, near: not near, near=lambda k, t: k == 0 and t >= size - 64 * 8)
    assert 0 < obs["threat_moments"] < 20
    assert "excess" not in obs.index or np.isnan(obs["excess"])
    assert events == []
