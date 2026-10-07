"""Scripted bunnyhopping: perfect rehops in chains (detectors/bunnyhop.py)."""

import numpy as np

from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.testing.synthetic import SynthPlayer, static, wall_scenario

OBS = 1001


def hops(T, n_chains, hops_per_chain, ground_ticks, arc=40, rest=200):
    """airborne mask: chains of jumps separated by `ground_ticks` on the ground, chains `rest` ticks apart"""
    air = np.zeros(T, dtype=bool)
    t = 100
    for _ in range(n_chains):
        for _ in range(hops_per_chain):
            if t + arc >= T:
                return air
            air[t:t + arc] = True
            t += arc + ground_ticks
        t += rest
    return air


def run(config, air):
    sc = wall_scenario(300)
    T = sc.T
    sc.add(SynthPlayer(OBS, 3, "o", static(T, (0, 0, 0)), np.zeros(T), np.zeros(T), airborne=air))
    demo = sc.to_demo()
    *_, ctx, events = build_analysis(demo, config, detector_names=["bunnyhop"], geometry=sc.geometry())
    obs = ctx.observations.frame("bunnyhop")
    return [e for e in events if e.steam_id == OBS], obs[obs.steam_id == OBS].iloc[0]


def test_scripted_chains_fire(config):
    events, obs = run(config, hops(300 * 64, n_chains=25, hops_per_chain=4, ground_ticks=1))
    assert obs["perfect_rehops"] == 75 and obs["perfect_share"] == 1.0 and obs["chains"] == 25
    assert len(events) == 1 and events[0].context["scope"] == "match"
    assert events[0].metrics["longest_chain"] == 4


def test_human_timing_does_not(config):
    """The same number of rehops, but 6 ticks on the ground each time."""
    events, obs = run(config, hops(300 * 64, n_chains=25, hops_per_chain=4, ground_ticks=6))
    assert obs["rehops"] == 75 and obs["perfect_rehops"] == 0
    assert events == []


def test_a_few_perfect_chains_are_not_enough(config):
    events, obs = run(config, hops(300 * 64, n_chains=5, hops_per_chain=4, ground_ticks=1))
    assert obs["perfect_rehops"] == 15 and obs["chains"] == 5
    assert events == []
