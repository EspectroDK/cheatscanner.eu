"""Synthetic behavior scenarios (spec section 30).

The observer-moving / enemy-stationary case is the key false-positive guard.
"""

import numpy as np
import pytest

from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.testing.synthetic import SynthPlayer, aim_at, static, wall_scenario, weave

OBS, ENEMY = 1001, 2001
HIDDEN_START_S = 16.0  # after the round-start predictability window


def run(sc, config, detectors=("hidden_tracking",)):
    demo = sc.to_demo()
    world, geometry, smoke, vis, knowledge, enc, ctx, events = build_analysis(
        demo, config, detector_names=list(detectors), geometry=sc.geometry())
    return events, ctx


def events_of(events, detector, sid=OBS):
    return [e for e in events if e.detector_type == detector and e.steam_id == sid]


def build(observer_pos, enemy_pos, aim_target, lag=10, noise=0.3, seconds=30):
    sc = wall_scenario(seconds)
    T = sc.T
    obs_eye = observer_pos.copy()
    obs_eye[:, 2] += 64
    pitch, yaw = aim_at(obs_eye, aim_target, lag_ticks=lag, noise_deg=noise)
    sc.add(SynthPlayer(OBS, 3, "observer", observer_pos, pitch, yaw))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", enemy_pos, np.zeros(T), np.full(T, 180.0)))
    return sc


def head(pos):
    h = pos.copy()
    h[:, 2] += 64
    return h


def test_hidden_enemy_stationary_not_flagged(config):
    sc = wall_scenario()
    T = sc.T
    enemy = static(T, (800, 100, 0))
    observer = static(T, (0, 0, 0))
    # crosshair resting on the (memorised) stationary enemy position
    sc = build(observer, enemy, head(enemy))
    events, ctx = run(sc, config)
    assert events_of(events, "hidden_tracking") == []


def test_hidden_enemy_moving_left_tracked_is_flagged(config):
    sc0 = wall_scenario()
    T = sc0.T
    enemy = weave(T, sc0.tickrate, 800, 0, 250, 3.0, start=sc0.s(HIDDEN_START_S))
    observer = static(T, (0, 0, 0))
    sc = build(observer, enemy, head(enemy))
    events, ctx = run(sc, config)
    ev = events_of(events, "hidden_tracking")
    assert ev, "tracking a moving hidden enemy must produce evidence"
    top = max(ev, key=lambda e: e.severity)
    assert top.metrics["tracking_corr"] > 0.9
    assert top.target_steam_id == ENEMY
    assert top.context["knowledge"] == "UNKNOWN"


def test_observer_moving_enemy_stationary_not_flagged(config):
    """Bearing to the enemy changes only because the observer strafes.

    The crosshair stays on the enemy (as it would for someone holding a known
    spot), so aim deltas correlate with the *total* bearing change - but the
    target-induced component is zero, so nothing may be flagged.
    """
    sc0 = wall_scenario()
    T = sc0.T
    enemy = static(T, (800, 0, 0))
    observer = weave(T, sc0.tickrate, 0, 0, 250, 3.0, start=sc0.s(HIDDEN_START_S))
    observer[:, 0] = 0
    sc = build(observer, enemy, head(enemy))
    events, ctx = run(sc, config)
    assert events_of(events, "hidden_tracking") == []
    obs = ctx.observations.frame("hidden_tracking")
    mine = obs[obs.steam_id == OBS]
    assert (mine["target_angular_path_deg"] < 1.0).all()
    assert (mine["observer_induced_path_deg"] > 10).any()


def test_tracking_last_known_position_not_flagged(config):
    sc0 = wall_scenario()
    T = sc0.T
    enemy = weave(T, sc0.tickrate, 800, 0, 250, 3.0, start=sc0.s(HIDDEN_START_S))
    observer = static(T, (0, 0, 0))
    last_known = static(T, enemy[sc0.s(HIDDEN_START_S)])
    sc = build(observer, enemy, head(last_known))
    events, _ = run(sc, config)
    assert events_of(events, "hidden_tracking") == []


def test_tracking_with_sound_information_not_flagged(config):
    """Same tracking, but the enemy is running (audible footsteps): legitimate information."""
    sc0 = wall_scenario()
    T = sc0.T
    enemy = weave(T, sc0.tickrate, 800, 0, 250, 1.5, start=sc0.s(HIDDEN_START_S))
    observer = static(T, (0, 0, 0))
    sc = build(observer, enemy, head(enemy))
    sc.players[1].walking = False  # running -> footsteps
    events, _ = run(sc, config)
    assert events_of(events, "hidden_tracking") == []


def test_previsibility_convergence_flagged(config):
    """Enemy walks behind the wall and steps out at x=... ; aim converges on its hidden position first."""
    sc = wall_scenario(30)
    T = sc.T
    t = np.arange(T) / sc.tickrate
    # enemy walks along y behind the wall and emerges past its end (wall spans y in [-2000, 2000] -> shorten)
    sc.walls = [((400, -2000, -100), (420, 300, 400))]
    y = np.clip(-600 + (t - HIDDEN_START_S) * 120, -600, 900)
    enemy = np.stack([np.full(T, 800.0), y, np.zeros(T)], axis=1)
    observer = static(T, (0, 0, 0))
    obs_eye = head(observer)
    pitch, yaw = aim_at(obs_eye, head(enemy), lag_ticks=8)
    # enemy becomes visible at ~26 s (line of sight clears the wall end at y=600);
    # the crosshair is 25 deg off until 23.5 s and converges during the last ~2 s
    far = t < 23.5
    yaw = np.where(far, yaw - 25.0, yaw)
    blend = np.clip((t - 23.5) / 2.2, 0, 1)
    yaw = np.where(~far, yaw - 25.0 * (1 - blend), yaw)
    sc.add(SynthPlayer(OBS, 3, "observer", observer, pitch, yaw))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", enemy, np.zeros(T), np.full(T, 180.0)))
    events, ctx = run(sc, config, detectors=("previsibility",))
    obs = ctx.observations.frame("previsibility")
    assert len(obs[obs.steam_id == OBS]) >= 1
    ev = events_of(events, "previsibility")
    assert ev, str(obs[obs.steam_id == OBS].iloc[0].to_dict())
    assert ev[0].metrics["aim_driven_fraction"] > 0.5


def test_holding_common_angle_not_flagged_as_previsibility(config):
    """Crosshair parked on the corner where the enemy appears: legitimate pre-aim."""
    sc = wall_scenario(30)
    T = sc.T
    t = np.arange(T) / sc.tickrate
    sc.walls = [((400, -2000, -100), (420, 300, 400))]
    y = np.clip(-600 + (t - HIDDEN_START_S) * 120, -600, 900)
    enemy = np.stack([np.full(T, 800.0), y, np.zeros(T)], axis=1)
    observer = static(T, (0, 0, 0))
    corner = static(T, (800, 330, 64))
    pitch, yaw = aim_at(head(observer), corner, noise_deg=0.2)
    sc.add(SynthPlayer(OBS, 3, "observer", observer, pitch, yaw))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", enemy, np.zeros(T), np.full(T, 180.0)))
    events, _ = run(sc, config, detectors=("previsibility",))
    assert events_of(events, "previsibility") == []


def test_preaim_swept_onto_appearance_spot_not_flagged(config):
    """Crosshair moved onto the corner where the enemy appears (habit, a spot hot earlier in the match),
    not onto the enemy's moving hidden position: legitimate pre-aim, no event."""
    sc = wall_scenario(30)
    T = sc.T
    t = np.arange(T) / sc.tickrate
    sc.walls = [((400, -2000, -100), (420, 300, 400))]
    y = np.clip(-600 + (t - HIDDEN_START_S) * 120, -600, 900)
    enemy = np.stack([np.full(T, 800.0), y, np.zeros(T)], axis=1)
    observer = static(T, (0, 0, 0))
    corner = static(T, (800, 600, 64))
    pitch, yaw = aim_at(head(observer), corner)
    # 60 deg off the corner until 23.5 s, then swept onto it over ~2 s, as in the convergence test, but
    # from the side away from the enemy's approach so the sweep does not pass over him by chance
    blend = np.clip((t - 23.5) / 2.2, 0, 1)
    yaw = yaw + 60.0 * (1 - blend)
    sc.add(SynthPlayer(OBS, 3, "observer", observer, pitch, yaw))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", enemy, np.zeros(T), np.full(T, 180.0)))
    events, ctx = run(sc, config, detectors=("previsibility",))
    obs = ctx.observations.frame("previsibility")
    o = obs[obs.steam_id == OBS]
    assert len(o) >= 1 and o.iloc[0]["aim_driven_fraction"] > 0.5
    assert o.iloc[0]["static_spot_specificity_deg"] <= 0.5
    assert events_of(events, "previsibility") == []


def test_remembered_position_real_time_vs_memory(config):
    sc0 = wall_scenario(60)
    T = sc0.T
    enemy = weave(T, sc0.tickrate, 800, 0, 300, 4.0, start=sc0.s(HIDDEN_START_S))
    observer = static(T, (0, 0, 0))
    # cheater: current position; legit: 2s-old position
    sc = build(observer, enemy, head(enemy), seconds=60)
    ev, ctx = run(sc, config, detectors=("remembered_position",))
    cheat = ctx.observations.frame("remembered_position")
    c = cheat[cheat.steam_id == OBS].iloc[0]
    assert c["advantage_2000ms_deg"] > 3

    lagged = np.r_[np.repeat(enemy[:1], sc0.s(2.0), axis=0), enemy[:-sc0.s(2.0)]]
    sc2 = build(observer, enemy, head(lagged), seconds=60)
    ev2, ctx2 = run(sc2, config, detectors=("remembered_position",))
    legit = ctx2.observations.frame("remembered_position")
    l2 = legit[legit.steam_id == OBS].iloc[0]
    assert l2["advantage_2000ms_deg"] < 0
    assert not events_of(ev2, "remembered_position")
