"""Memory-saving parse paths give the same tables as the plain ones."""

import numpy as np
import pandas as pd
import pytest

from cs2_analyzer.parser.demoparser2_backend import TICK_PROPS, DemoParser2Backend, _Prefetched
from cs2_analyzer.parser.normalize import AIM_PUNCH_SERVICES_PROP, derive_velocity, normalize_ticks
from cs2_analyzer.world import _dense, _dense_labels


class FakeParser:
    """``parse_ticks`` over a fixed table, honouring ``ticks=`` like demoparser2."""

    def __init__(self, table: pd.DataFrame):
        self.table = table
        self.calls = []

    def parse_ticks(self, props, ticks=None):
        self.calls.append(None if ticks is None else (min(ticks), max(ticks)))
        t = self.table if ticks is None else self.table[self.table["tick"].isin(ticks)]
        return t.reset_index(drop=True).copy()


def _raw(n_ticks=10, players=(76561198000000001, 76561198000000002, 0)):
    rows = []
    for tick in range(1, n_ticks + 1):
        for i, sid in enumerate(players):
            rows.append({
                "X": np.float32(tick * 3 + i), "Y": np.float32(i), "Z": np.float32(0), "pitch": np.float32(1),
                "yaw": np.float32(170 + tick), "health": 100.0, "team_num": 2.0 + i % 2, "is_alive": True,
                "active_weapon_name": "AK-47" if tick % 2 else None, "m_iClip1": 30.0,
                "flash_duration": np.float32(0), "flash_max_alpha": np.float32(0),
                "is_scoped": None if tick == 3 else False,
                "duck_amount": np.float32(0.25 * (tick % 4)), "ducking": False, "is_walking": tick % 3 == 0,
                "is_airborne": False, "shots_fired": 0.0, "FIRE": None if tick == 2 else tick % 2 == 0,
                "spotted": False, "approximate_spotted_by": [players[1 - i]] if i < 2 and tick > 5 else [],
                "game_time": np.float32(tick / 64), "team_rounds_total": 0.0,
                AIM_PUNCH_SERVICES_PROP: [0.1 * tick, -0.2 * i, 0.0],
                "tick": np.int32(tick), "steamid": np.uint64(sid), "name": f"p{i}",
            })
    return pd.DataFrame(rows)


def test_tick_slices_give_the_same_table_as_one_call():
    table = _raw(n_ticks=10)
    parser = FakeParser(table)
    sliced = DemoParser2Backend._parse_ticks(parser, last_event_tick=7, chunk=3)
    assert parser.calls == [(0, 2), (3, 5), (6, 8), (9, 11)]  # stops in the slice where the demo ends
    whole = table[table["steamid"] != 0].assign(steamid=lambda d: d["steamid"].astype("int64"))
    rounds = pd.DataFrame()
    a = normalize_ticks(sliced, rounds, 64.0)
    b = normalize_ticks(whole.reset_index(drop=True), rounds, 64.0)
    pd.testing.assert_frame_equal(a, b)
    assert a.attrs["missing_props"] == b.attrs["missing_props"]
    assert "name" not in sliced and "ducking" not in sliced and AIM_PUNCH_SERVICES_PROP not in sliced
    assert sliced["FIRE"].dtype == bool and sliced["is_scoped"].dtype == bool


def test_tick_slices_continue_past_a_gap_before_the_last_event():
    table = _raw(n_ticks=12)
    table = table[(table["tick"] < 4) | (table["tick"] > 8)]  # no rows in ticks 4-8
    parser = FakeParser(table)
    sliced = DemoParser2Backend._parse_ticks(parser, last_event_tick=12, chunk=3)
    assert sorted(sliced["tick"].unique()) == [1, 2, 3, 9, 10, 11, 12]


def test_normalize_orders_rows_and_derives_velocity_like_derive_velocity():
    raw = _raw(n_ticks=8).sample(frac=1, random_state=3).reset_index(drop=True)  # shuffled rows
    raw = raw[raw["steamid"] != 0].reset_index(drop=True)
    t = normalize_ticks(raw, pd.DataFrame(), 64.0)
    assert (np.diff(t["tick"].to_numpy()) >= 0).all()
    ref = derive_velocity(t.drop(columns=["velocity_x", "velocity_y", "velocity_z", "speed_2d"]), 64.0)
    ref = ref.sort_values(["tick", "steam_id"]).reset_index(drop=True)
    for c in ("velocity_x", "velocity_y", "velocity_z", "speed_2d"):
        np.testing.assert_array_equal(t[c].to_numpy(), ref[c].to_numpy())


def test_dense_labels_match_dense_with_shared_strings():
    df = pd.DataFrame({"w": pd.Series(["ak47", None, "m4a1", "ak47"], dtype="str")})
    pidx, tidx = np.array([0, 0, 1, 1]), np.array([0, 1, 0, 2])
    a = _dense_labels(df, "w", pidx, tidx, (2, 3), None)
    b = _dense(df, "w", pidx, tidx, (2, 3), None, object)
    assert repr(a.tolist()) == repr(b.tolist())
    assert a[0, 0] is a[1, 2]  # one object per distinct name


def test_prefetched_events_refuse_unlisted_names():
    class P:
        def parse_events(self, names):
            return [("player_death", pd.DataFrame({"tick": [5]}))]

        def parse_player_info(self):
            return pd.DataFrame()

    cached = _Prefetched(P(), {"player_death", "round_end"})
    assert len(cached.parse_event("player_death")) == 1
    assert not len(cached.parse_event("round_end"))  # listed, but the demo has none
    with pytest.raises(KeyError):
        cached.parse_event("item_pickup")
    assert "ducking" in TICK_PROPS  # FIRE needs it
