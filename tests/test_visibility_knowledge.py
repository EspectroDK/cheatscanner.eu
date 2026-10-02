import numpy as np
import pandas as pd

from cs2_analyzer.geometry.smoke import SmokeModel, SmokeParams, _ellipsoid_chord
from cs2_analyzer.geometry.visibility import LOS, VisibilityEngine
from cs2_analyzer.knowledge.model import Knowledge, KnowledgeModel, _last_true_index
from cs2_analyzer.testing.synthetic import SynthPlayer, aim_at, static, wall_scenario
from cs2_analyzer.world import build_world

OBS, ENEMY = 1001, 2001


def scenario(enemy_y=0.0, seconds=20, walking=True, walls=True):
    sc = wall_scenario(seconds)
    if not walls:
        sc.walls = []
    T = sc.T
    obs = static(T, (0, 0, 0))
    en = static(T, (800, enemy_y, 0))
    eye = obs.copy()
    eye[:, 2] += 64
    tgt = en.copy()
    tgt[:, 2] += 64
    p, y = aim_at(eye, tgt)
    sc.add(SynthPlayer(OBS, 3, "o", obs, p, y))
    sc.add(SynthPlayer(ENEMY, 2, "e", en, np.zeros(T), np.full(T, 180.0), walking=walking))
    return sc


def engine(sc, smokes=None):
    demo = sc.to_demo()
    if smokes is not None:
        demo.events["smokes"] = smokes
    w = build_world(demo)
    sm = SmokeModel(demo.event("smokes"), w.tick0, w.T, w.tickrate, SmokeParams())
    vis = VisibilityEngine(w, sc.geometry(), sm).compute()
    return demo, w, vis


def test_wall_occludes_and_open_line_is_visible():
    _, w, vis = engine(scenario(enemy_y=0))
    t = w.T // 2
    assert vis.state(0, 1, t) == LOS.GEOMETRY_OCCLUDED
    _, w2, vis2 = engine(scenario(enemy_y=0, walls=False))
    assert vis2.state(0, 1, t) == LOS.DIRECT_VISIBLE
    assert vis2.in_fov[0, 1, t]


def test_near_edge_is_unknown_not_hidden():
    sc = scenario(enemy_y=0)
    sc.walls = [((400, -2000, -100), (420, 0, 400))]  # wall ends right at the line of sight
    _, w, vis = engine(sc)
    assert vis.state(0, 1, w.T // 2) in (LOS.UNKNOWN, LOS.DIRECT_VISIBLE)


def test_smoke_occludes_with_uncertain_phases():
    sc = scenario(enemy_y=0, walls=False)
    T = sc.T
    smokes = pd.DataFrame([{"entity_id": 1, "start_tick": 1 + 64, "end_tick": 1 + 64 * 15, "x": 400.0, "y": 0.0,
                            "z": 0.0, "thrower_steam_id": 0}])
    _, w, vis = engine(sc, smokes)
    assert vis.state(0, 1, 64 * 8) == LOS.SMOKE_OCCLUDED
    # bloom phase right after detonation is uncertain, not opaque
    assert vis.state(0, 1, 64 + 5) == LOS.VISIBLE_THROUGH_SMOKE
    assert vis.state(0, 1, T - 10) == LOS.DIRECT_VISIBLE


def test_ellipsoid_chord():
    a = np.array([[-500.0, 0, 0]])
    b = np.array([[500.0, 0, 0]])
    assert abs(_ellipsoid_chord(a, b, np.zeros(3), 100.0, 50.0)[0] - 200.0) < 1e-6
    miss = _ellipsoid_chord(np.array([[-500.0, 0, 200]]), np.array([[500.0, 0, 200]]), np.zeros(3), 100.0, 50.0)
    assert miss[0] == 0


def test_knowledge_levels_and_sound():
    sc = scenario(enemy_y=0, seconds=30)
    demo, w, vis = engine(sc)
    kn = KnowledgeModel(w, vis, demo.events).compute()
    t_late = w.T - 10
    assert kn.level[0, 1, 64 * 5] == Knowledge.POSSIBLY_KNOWN  # round-start predictability
    assert kn.level[0, 1, t_late] == Knowledge.UNKNOWN
    # the same situation with the enemy firing a gun nearby: LIKELY_KNOWN
    sc2 = scenario(enemy_y=0, seconds=30)
    sc2.add_event("shots", tick=1 + w.T - 40, steam_id=ENEMY, weapon="weapon_ak47")
    demo2, w2, vis2 = engine(sc2)
    kn2 = KnowledgeModel(w2, vis2, demo2.events).compute()
    assert kn2.level[0, 1, t_late] == Knowledge.LIKELY_KNOWN
    d = kn2.describe(0, 1, t_late)
    assert d["possible_sound"] is True


def test_visibility_check_api_shape():
    sc = scenario(enemy_y=0)
    demo, w, vis = engine(sc)
    KnowledgeModel(w, vis, demo.events).compute()
    r = vis.check(OBS, ENEMY, w.tick(w.T - 5))
    for key in ("direct_los", "geometry_blocked", "smoke_blocked", "teammate_los", "last_seen_ms", "possible_sound",
                "knowledge_confidence"):
        assert key in r
    assert r["geometry_blocked"] and not r["direct_los"]
    assert 0 < r["knowledge_confidence"] <= 1


def test_last_true_index_resets_per_round():
    m = np.array([0, 1, 0, 0, 0, 0], bool)
    reset = np.array([1, 0, 0, 1, 0, 0], bool)
    out = _last_true_index(m, reset)
    assert out[2] == 1 and out[4] < 0


def test_gunfire_hole_clears_only_a_tunnel():
    from cs2_analyzer.geometry.smoke import _segment_distance

    smokes = pd.DataFrame([{"entity_id": 1, "start_tick": 0, "end_tick": 64 * 20, "x": 400.0, "y": 0.0, "z": -55.0,
                            "thrower_steam_id": 0}])
    sm = SmokeModel(smokes, 0, 64 * 20, 64.0, SmokeParams())
    t = np.array([64 * 5])
    # a shot straight through the smoke along y = 0
    sm.add_gunfire_holes(np.array([[0.0, 0.0, 0.0]]), np.array([[1.0, 0.0, 0.0]]), t)
    along = sm.occlusion(np.array([[0.0, 10.0, 0.0]]), np.array([[800.0, 10.0, 0.0]]), t + 10)
    beside = sm.occlusion(np.array([[0.0, 0.0, 0.0]]), np.array([[800.0, 150.0, 0.0]]), t + 10)
    later = sm.occlusion(np.array([[0.0, 10.0, 0.0]]), np.array([[800.0, 10.0, 0.0]]), t + 200)
    assert not along[0][0] and along[1][0]  # near the bullet tunnel: uncertain
    assert beside[0][0]  # the rest of the smoke stays opaque
    assert later[0][0]  # the tunnel closes again
    d = _segment_distance(np.array([[0.0, 0.0, 0.0]]), np.array([[10.0, 0.0, 0.0]]),
                          np.array([5.0, 3.0, 4.0]), np.array([5.0, 3.0, 40.0]))
    assert abs(d[0] - 5.0) < 1e-9


def test_unpaired_smoke_expiry_falls_back_to_normal_duration():
    # expiry before the detonation (reused entity id) or far too late: assume about 22 s
    smokes = pd.DataFrame([{"entity_id": 1, "start_tick": 1000, "end_tick": 500, "x": 0.0, "y": 0.0, "z": 0.0,
                            "thrower_steam_id": 0},
                           {"entity_id": 2, "start_tick": 1000, "end_tick": 90000, "x": 0.0, "y": 0.0, "z": 0.0,
                            "thrower_steam_id": 0}])
    sm = SmokeModel(smokes, 0, 100000, 64.0, SmokeParams())
    assert [it["end"] - it["start"] for it in sm.items] == [22 * 64, 22 * 64]
