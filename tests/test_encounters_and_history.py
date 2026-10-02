import numpy as np

from cs2_analyzer.detectors.base import EvidenceEvent
from cs2_analyzer.detectors.common import last_known_positions
from cs2_analyzer.encounters.segmentation import encounter_frame, segment_encounters
from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.scoring.aggregate import assess_history, assess_match
from cs2_analyzer.testing.synthetic import SynthPlayer, aim_at, static, wall_scenario

OBS, ENEMY = 1001, 2001


def peek_scenario():
    """Enemy walks out from behind a wall end at ~t=12s; observer fires at 12.5s."""
    sc = wall_scenario(20)
    sc.walls = [((400, -2000, -100), (420, 300, 400))]
    T = sc.T
    t = np.arange(T) / sc.tickrate
    y = np.clip(-400 + (t - 4) * 100, -400, 1000)
    enemy = np.stack([np.full(T, 800.0), y, np.zeros(T)], axis=1)
    observer = static(T, (0, 0, 0))
    eye = observer.copy()
    eye[:, 2] += 64
    head = enemy.copy()
    head[:, 2] += 64
    p, yaw = aim_at(eye, head, lag_ticks=6)
    sc.add(SynthPlayer(OBS, 3, "o", observer, p, yaw))
    sc.add(SynthPlayer(ENEMY, 2, "e", enemy, np.zeros(T), np.full(T, 180.0)))
    sc.add_event("shots", tick=1 + sc.s(14.5), steam_id=OBS, weapon="weapon_ak47")
    return sc


def test_encounter_extraction_first_seen_and_shot(config):
    sc = peek_scenario()
    demo = sc.to_demo()
    world, geo, smoke, vis, kn, enc, ctx, ev = build_analysis(demo, config, detector_names=["snap"], geometry=sc.geometry())
    mine = [e for e in enc if world.steam_ids[e.observer] == OBS]
    assert len(mine) == 1
    e = mine[0]
    assert e.anchor_type == "FIRST_SEEN"
    assert e.t_first_shot is not None and e.t_first_shot > e.t_first_seen
    assert e.t_anchor - e.t_start == world.ticks_for_ms(2000)
    frame = encounter_frame(world, vis, kn, e)
    assert {"aim_error", "angular_velocity", "visibility", "knowledge", "recoil_pitch"} <= set(frame.columns)
    assert len(frame) == e.t_end - e.t_start + 1
    # visibility flips from occluded to visible at the anchor
    assert frame.loc[frame.rel_ms < -100, "visibility"].max() >= 4 or (frame.loc[frame.rel_ms < -100, "visibility"] != 1).all()
    assert frame.loc[frame.rel_ms.between(0, 200), "visibility"].eq(1).all()


def test_historical_position_lookup(config):
    sc = peek_scenario()
    demo = sc.to_demo()
    world, geo, smoke, vis, kn, enc, ctx, ev = build_analysis(demo, config, detector_names=["snap"], geometry=sc.geometry())
    o, e = world.index_of[OBS], world.index_of[ENEMY]
    # after the enemy is seen and then... last-known position = position at last info tick
    t = world.T - 1
    lk, has = last_known_positions(ctx, o, e, np.array([t]))
    assert has[0]
    last = kn.last_info[o, e, t]
    np.testing.assert_allclose(lk[0], world.eye[e, last])


def _ev(sid, det, axis, group, sev, t0, target=2, rnd=1, scope=None):
    return EvidenceEvent(match_id="m", steam_id=sid, round_number=rnd, tick_start=t0, tick_peak=t0 + 10, tick_end=t0 + 100,
                         detector_type=det, severity=sev, reliability=1.0, information_confidence=1.0, evidence_axis=axis,
                         evidence_group=group, target_steam_id=target, context={"scope": scope} if scope else {})


CFG = {
    "incident_merge_gap_ms": 3000, "agreement_bonus": 0.1, "min_incidents": 2, "min_incident_strength": 0.15,
    "single_incident_cap": 0.24, "within_family_secondary_weight": 0.25, "corroboration_min_family": 0.3,
    "corroboration_bonus": 0.15, "min_encounters": 1, "min_rounds": 1,
    "axis_weights": {"HIDDEN_INFORMATION": 1.0, "AIM_MECHANICS": 1.0},
}


def test_single_spectacular_event_is_capped():
    a = assess_match(1, "m", [_ev(1, "hidden_tracking", "HIDDEN_INFORMATION", "information", 0.99, 1000)], 50, 10, CFG)
    assert a.axis_scores["HIDDEN_INFORMATION"] <= 0.24
    assert a.classification == "NORMAL"
    # the same incident with a somewhat unusual player profile does reach ELEVATED
    profile = {"score": 5.0, "clean_percentile": 0.97, "strength": 0.1, "features": {}}
    b = assess_match(1, "m", [_ev(1, "hidden_tracking", "HIDDEN_INFORMATION", "information", 0.99, 1000)], 50, 10, CFG,
                     profile=profile)
    assert b.classification == "ELEVATED"


def test_same_incident_not_triple_counted():
    same = [
        _ev(1, "hidden_tracking", "HIDDEN_INFORMATION", "information", 0.8, 1000),
        _ev(1, "previsibility", "HIDDEN_INFORMATION", "information", 0.8, 1050),
        _ev(1, "smoke_tracking", "HIDDEN_INFORMATION", "information", 0.8, 1100),
    ]
    a = assess_match(1, "m", same, 50, 10, CFG)
    assert len(a.incidents) == 1
    assert a.axis_scores["HIDDEN_INFORMATION"] <= 0.24  # still one incident -> capped
    separate = [_ev(1, "hidden_tracking", "HIDDEN_INFORMATION", "information", 0.8, t, rnd=r)
                for t, r in ((1000, 1), (20000, 2), (40000, 3))]
    b = assess_match(1, "m", separate, 50, 10, CFG)
    assert len(b.incidents) == 3
    assert b.axis_scores["HIDDEN_INFORMATION"] > 0.9


def test_independent_axes_corroborate_more_than_correlated():
    info = [_ev(1, "hidden_tracking", "HIDDEN_INFORMATION", "information", 0.5, t, rnd=r) for t, r in ((1000, 1), (30000, 2))]
    more_info = info + [_ev(1, "previsibility", "HIDDEN_INFORMATION", "information", 0.5, 60000, rnd=3),
                        _ev(1, "previsibility", "HIDDEN_INFORMATION", "information", 0.5, 90000, rnd=4)]
    mech = info + [_ev(1, "snap", "AIM_MECHANICS", "aim", 0.5, 60000, rnd=3), _ev(1, "snap", "AIM_MECHANICS", "aim", 0.5, 90000, rnd=4)]
    a_corr = assess_match(1, "m", more_info, 50, 10, CFG)
    a_indep = assess_match(1, "m", mech, 50, 10, CFG)
    assert a_indep.overall > a_corr.overall


def test_insufficient_data_label():
    a = assess_match(1, "m", [], 3, 2, {**CFG, "min_encounters": 15, "min_rounds": 5})
    assert a.classification == "INSUFFICIENT_DATA"


def test_history_shrinkage_and_no_accumulation_from_mild_matches():
    cfg = {"prior_matches": 3, "high_match_threshold": 0.5, "consistency_min_matches": 3, "consistency_weight": 0.25,
           "min_total_encounters": 10}
    row = lambda s: {"overall": s, "aim_score": 0, "hidden_information_score": s, "shot_timing_score": 0,  # noqa: E731
                     "recoil_score": 0, "decision_information_score": 0, "classification": "X", "encounters_analyzed": 40}
    one = assess_history([row(0.9)], cfg)
    assert one["historical_evidence_score"] < 0.25 and one["confidence_level"] == "LOW"
    mild = assess_history([row(0.3)] * 20, cfg)
    assert mild["historical_evidence_score"] < 0.3  # many mildly elevated legit matches do not accumulate
    strong = assess_history([row(0.85)] * 8, cfg)
    assert strong["historical_evidence_score"] > 0.75
    assert strong["high_severity_matches"] == 8
