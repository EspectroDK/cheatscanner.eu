"""View turning independently of the user command's mouse counts (detectors/mouse_view.py)."""

import numpy as np

from cs2_analyzer.detectors.mouse_view import mouse_view_stats
from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.testing.synthetic import SynthPlayer, static, wall_scenario

OBS = 1001
GAIN = 1.3 * 0.022


def human_input(T, seed=1, sampled=0.5):
    """Mouse counts per tick; the recorded command holds only part of the tick's counts (as in demos)."""
    rng = np.random.default_rng(seed)
    counts = np.round(rng.normal(0, 6, T) * (rng.random(T) < 0.6))
    recorded = np.round(counts * sampled + rng.normal(0, 0.7, T))
    yaw = np.cumsum(-counts * GAIN)
    pitch = np.clip(np.cumsum(np.round(rng.normal(0, 1, T)) * GAIN), -60, 60)
    return yaw, pitch, np.stack([recorded, np.zeros(T)], axis=1)


def run(config, yaw, pitch, mouse):
    sc = wall_scenario(300)
    T = sc.T
    sc.add(SynthPlayer(OBS, 3, "o", static(T, (0, 0, 0)), pitch[:T], yaw[:T], mouse=mouse[:T]))
    *_, ctx, events = build_analysis(sc.to_demo(), config, detector_names=["mouse_view"], geometry=sc.geometry())
    obs = ctx.observations.frame("mouse_view_summary")
    mine = obs[obs.steam_id == OBS] if len(obs) else obs
    return [e for e in events if e.steam_id == OBS], (mine.iloc[0] if len(mine) else None)


def test_human_mouse_explains_the_view(config):
    yaw, pitch, mouse = human_input(300 * 64)
    events, obs = run(config, yaw, pitch, mouse)
    assert obs["agreement"] > 0.9
    assert events == []


def test_absolute_angles_in_mouse_fields_fire(config):
    """Commands whose mouse fields hold the view angle itself (dx = -yaw / 0.022, dy = pitch / 0.022)."""
    yaw, pitch, _ = human_input(300 * 64)
    wrapped = (yaw + 180) % 360 - 180
    mouse = np.stack([np.round(-wrapped / 0.022), np.round(pitch / 0.022)], axis=1)
    events, obs = run(config, yaw, pitch, mouse)
    assert obs["absolute_share"] > 0.9
    assert len(events) == 1 and events[0].context["scope"] == "match"


def test_unrelated_mouse_counts_alone_do_not_fire(config):
    """Plain mouse counts that do not explain the view are recorded (agreement) but are no evidence."""
    yaw, pitch, _ = human_input(300 * 64, seed=1)
    _, _, other = human_input(300 * 64, seed=2)
    events, obs = run(config, yaw, pitch, other)
    assert obs["agreement"] < 0.05 and obs["absolute_share"] < 0.01
    assert events == []


def test_no_mouse_data_produces_nothing(config):
    yaw, pitch, mouse = human_input(300 * 64)
    events, obs = run(config, yaw, pitch, np.full_like(mouse, np.nan))
    assert obs is None
    assert events == []


def test_stats_on_plain_arrays():
    T = 5000
    yaw, pitch, mouse = human_input(T)
    s = mouse_view_stats((yaw + 180) % 360 - 180, pitch, mouse[:, 0], mouse[:, 1], np.ones(T, dtype=bool))
    assert s["agreement"] > 0.9 and s["absolute_share"] < 0.01
    assert abs(s["gain_yaw"] / GAIN - 2.0) < 0.3  # recorded command holds half the counts
