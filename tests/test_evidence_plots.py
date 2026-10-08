from types import SimpleNamespace

import numpy as np
import pandas as pd

from cs2_analyzer.evidence.plots import classify_shots


def _result(shot_ticks, hurts):
    shot_mask = np.zeros((3, 200), dtype=bool)
    shot_mask[0, shot_ticks] = True
    world = SimpleNamespace(shot_mask=shot_mask, steam_ids=[11, 22, 33], index_of={11: 0, 22: 1, 33: 2}, tick0=1000)
    demo = SimpleNamespace(event=lambda name: pd.DataFrame(hurts) if name == "hurts" else pd.DataFrame())
    return SimpleNamespace(world=world, demo=demo)


def _hurt(tick, victim, attacker=11, weapon="ak47"):
    return {"tick": 1000 + tick, "attacker_steam_id": attacker, "victim_steam_id": victim, "weapon": weapon}


def test_shots_are_split_into_hits_on_the_target_other_hits_and_misses():
    result = _result([10, 20, 30, 40, 50], [
        _hurt(11, 22),                  # shot 10 hits the target a tick later
        _hurt(20, 33),                  # shot 20 hits someone else
        _hurt(39, 22),                  # before shot 40: belongs to shot 30, which is too long ago
        _hurt(50, 22, attacker=33),     # someone else's damage
        _hurt(51, 22, weapon="hegrenade"),
    ])
    assert classify_shots(result, 0, 1, 0, 199) == [(10, "target"), (20, "other"), (30, "miss"), (40, "miss"),
                                                    (50, "miss")]
    assert classify_shots(result, 0, 1, 15, 35) == [(20, "other"), (30, "miss")]


def test_without_damage_events_every_shot_is_a_miss():
    result = _result([5], [])
    assert classify_shots(result, 0, None, 0, 199) == [(5, "miss")]
