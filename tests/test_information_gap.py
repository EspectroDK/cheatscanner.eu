"""Information with a margin of error (knowledge/estimates.py, detectors/information_gap.py)."""

import numpy as np

from cs2_analyzer.config import Config
from cs2_analyzer.knowledge.estimates import EstimateParams, InformationSources, information_gap
from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.testing.synthetic import SynthPlayer, aim_at, static, wall_scenario, weave

OBS, ENEMY = 1001, 2001
START_S = 16.0


def head(pos):
    h = pos.copy()
    h[:, 2] += 64
    return h


def scenario(enemy, aim_target, running=True, lag=6, seconds=30):
    sc = wall_scenario(seconds)
    observer = static(sc.T, (0, 0, 0))
    eye = observer.copy()
    eye[:, 2] += 64
    pitch, yaw = aim_at(eye, aim_target, lag_ticks=lag, noise_deg=0.2)
    sc.add(SynthPlayer(OBS, 3, "observer", observer, pitch, yaw))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", enemy, np.zeros(sc.T), np.full(sc.T, 180.0), walking=not running))
    return sc


def run(sc, config):
    demo = sc.to_demo()
    world, geometry, smoke, vis, knowledge, enc, ctx, events = build_analysis(
        demo, config, detector_names=["information_gap"], geometry=sc.geometry())
    return events, ctx


def totals(ctx):
    obs = ctx.observations.frame("information_gap")
    mine = obs[obs.steam_id == OBS]
    return float(mine["tracked_deg"].sum()), float(mine["opportunity_deg"].sum())


def low_thresholds(tmp_path):
    return Config.load(overrides={
        "output": {"dir": str(tmp_path / "out"), "observations_dir": str(tmp_path / "obs")},
        "detectors": {"information_gap": {"min_tracked_deg": 20.0, "min_tracked_share": 0.2}},
    })


def test_gap_grows_with_distance_from_last_sighting():
    from cs2_analyzer.world import build_world

    sc = wall_scenario(30)
    T = sc.T
    enemy = weave(T, sc.tickrate, 800, 0, 250, 6.0, start=sc.s(START_S))
    sc.add(SynthPlayer(OBS, 3, "observer", static(T, (0, 0, 0)), np.zeros(T), np.zeros(T)))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", enemy, np.zeros(T), np.full(T, 180.0)))
    world = build_world(sc.to_demo())
    o, e = world.index_of[OBS], world.index_of[ENEMY]
    t0 = sc.s(START_S)
    seen = np.full(T, -(1 << 30), dtype=np.int32)
    seen[t0:] = t0  # last seen at t0, never again
    none = np.full(T, -(1 << 30), dtype=np.int32)
    last = {"sight": seen, "team": none, "sound": none, "damage": none}
    idx = np.arange(t0 + 1, t0 + sc.s(1.5))
    gap, src = information_gap(world, last, o, e, idx, EstimateParams(extrapolate_s=0.0))
    assert gap[0] < 1.0  # just seen
    assert gap[-1] > 10.0  # moved ~250 units sideways since
    assert (src == 0).all()
    # sound right now still leaves the floor
    snd = np.arange(T, dtype=np.int32)
    gap2, src2 = information_gap(world, {**last, "sound": snd}, o, e, idx, EstimateParams(sound_floor_deg=6.0))
    assert np.all(gap2 <= 6.5)  # heard one tick ago
    assert np.all(gap2[-10:] >= 6.0 - 1e-6)


def test_tracking_running_enemy_through_wall_is_counted(tmp_path):
    """Footsteps say roughly where he is; following his head closely is beyond that."""
    sc0 = wall_scenario()
    enemy = weave(sc0.T, sc0.tickrate, 800, 0, 250, 3.0, start=sc0.s(START_S))
    events, ctx = run(scenario(enemy, head(enemy)), low_thresholds(tmp_path))
    tracked, opp = totals(ctx)
    assert opp > 50
    assert tracked / opp > 0.5
    ev = [e for e in events if e.steam_id == OBS]
    assert ev and ev[0].context["scope"] == "match"
    assert ev[0].metrics["tracked_share"] > 0.5


def test_aiming_where_the_sound_was_is_not_counted(tmp_path):
    """Following the footsteps with a human delay keeps the crosshair well off his head."""
    sc0 = wall_scenario()
    enemy = weave(sc0.T, sc0.tickrate, 800, 0, 250, 3.0, start=sc0.s(START_S))
    events, ctx = run(scenario(enemy, head(enemy), lag=40), low_thresholds(tmp_path))
    tracked, opp = totals(ctx)
    assert opp > 50
    assert tracked / opp < 0.15
    assert not [e for e in events if e.steam_id == OBS]


def test_holding_an_angle_on_a_stationary_enemy_is_not_counted(tmp_path):
    sc0 = wall_scenario()
    enemy = static(sc0.T, (800, 100, 0))
    events, ctx = run(scenario(enemy, head(enemy), running=False), low_thresholds(tmp_path))
    tracked, opp = totals(ctx)
    assert tracked < 5
    assert not [e for e in events if e.steam_id == OBS]


def test_default_thresholds_need_a_whole_match(config):
    """One 30 s stretch of perfect tracking is not enough for an event at the default thresholds."""
    sc0 = wall_scenario()
    enemy = weave(sc0.T, sc0.tickrate, 800, 0, 250, 3.0, start=sc0.s(START_S))
    events, ctx = run(scenario(enemy, head(enemy)), config)
    tracked, _ = totals(ctx)
    assert tracked < float(config.section("detectors.information_gap")["min_tracked_deg"])
    assert not [e for e in events if e.steam_id == OBS]


def test_sources_cover_every_pair(config):
    sc0 = wall_scenario()
    enemy = static(sc0.T, (800, 100, 0))
    _, ctx = run(scenario(enemy, head(enemy), running=False), config)
    src = InformationSources(ctx.knowledge)
    w = ctx.world
    last = src.last(w.index_of[OBS], w.index_of[ENEMY])
    assert set(last) == {"sight", "team", "sound", "damage"}
    assert all(len(v) == w.T for v in last.values())
