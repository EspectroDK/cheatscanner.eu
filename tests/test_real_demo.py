"""Optional regression test on a real demo.

Skipped unless ``CS2A_TEST_DEMO`` points to a .dem file. Expectations below are for the
public de_mirage Premier demo shipped as ``src/parser/test_demo.dem`` in
https://github.com/LaihoE/demoparser (patch 13984); for other demos only the
generic invariants apply. The demo is always kept (``keep_demo=True``).
"""

import os
from pathlib import Path

import numpy as np
import pytest

from cs2_analyzer.geometry.visibility import LOS
from cs2_analyzer.pipeline import analyze_demo
from cs2_analyzer.storage.repository import Database

DEMO = os.environ.get("CS2A_TEST_DEMO")
REFERENCE_SHA_PREFIX = "84a1a4191302bdd2a3bbb5a7"

pytestmark = pytest.mark.skipif(not DEMO or not Path(DEMO).is_file(), reason="set CS2A_TEST_DEMO=/path/to/demo.dem")


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    from cs2_analyzer.config import Config

    tmp = tmp_path_factory.mktemp("real")
    cfg = Config.load(overrides={
        "output": {"dir": str(tmp / "out"), "observations_dir": str(tmp / "obs")},
        "storage": {"database_url": f"sqlite:///{tmp}/t.sqlite"},
        "geometry": {"maps_dir": os.environ.get("CS2A_MAPS_DIR", "data/maps")},
    })
    db = Database(cfg.get("storage.database_url"))
    db.init_schema()
    res = analyze_demo(DEMO, cfg, db=db, keep_demo=True)
    return res, db


def test_generic_invariants(result):
    res, db = result
    assert Path(DEMO).is_file(), "demo must be kept with keep_demo=True"
    assert res.meta.tickrate > 0
    assert len(res.demo.players) >= 2
    steam_ids = [int(s) for s in res.demo.players["steam_id"]]
    assert all(s > 76561197960265728 for s in steam_ids), "SteamID64 precision lost"
    assert len(res.encounters) > 0
    for ev in res.events:
        assert ev.tick_start <= ev.tick_peak <= ev.tick_end
        assert 0 <= ev.confidence <= 1
    for a in res.assessments.values():
        assert a.classification in {"NORMAL", "ELEVATED", "HIGH", "VERY_HIGH", "INSUFFICIENT_DATA"}
    assert (res.output_dir / "match.json").is_file()
    assert db.get_match(res.meta.match_id) is not None


def test_reference_demo(result):
    res, _ = result
    if not res.meta.demo_sha256.startswith(REFERENCE_SHA_PREFIX):
        pytest.skip("not the reference demo")
    assert res.meta.map_name == "de_mirage"
    assert res.meta.tickrate == 64
    assert res.meta.mode == "premier"
    assert len(res.demo.players) == 10
    assert res.rounds["live"].sum() >= 9
    assert 250 <= len(res.encounters) <= 400
    checks = res.meta.extra["eye_position_check"]
    assert checks["eye_z_abs_err_median"] < 1.0
    assert checks["bullet_yaw_err_median_deg"] < 0.5
    # presumably legitimate match: nothing should reach HIGH with uncalibrated conservative thresholds
    assert not any(a.classification in ("HIGH", "VERY_HIGH") for a in res.assessments.values())
    if res.geometry.available:
        los = res.vis.los
        assert (los == LOS.DIRECT_VISIBLE).sum() > 5000
        # game spotting while we say geometry-occluded should only be spotting lag (within 32 ticks of our visible)
        sb = np.transpose(res.world.spotted_by, (1, 0, 2))
        vis = (los == LOS.DIRECT_VISIBLE) | (los == LOS.VISIBLE_THROUGH_SMOKE)
        for o, e, t in zip(*np.nonzero(sb & (los == LOS.GEOMETRY_OCCLUDED))):
            window = vis[o, e, max(0, t - 40):t + 41]
            assert window.any(), f"extra wall? observer {o} target {e} tick {res.world.tick(t)}"
