"""CS2CD dataset backend, on a tiny match written in the dataset's own format."""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cs2_analyzer.parser import backend_for_path, get_parser
from cs2_analyzer.parser.cs2cd_backend import cs2cd_steam_id, load_index, match_labels


def write_match(root: Path, split="with_cheater_present", n=7) -> Path:
    """Two players, warmup then two rounds (T wins, then CT wins)."""
    ticks = np.arange(1, 401)
    # round 1: prestart 50, freeze end 80, decided 150; round 2: prestart 200, freeze end 230, decided 300
    status = np.where((ticks >= 150) & (ticks < 200), 2, 0)
    status = np.where(ticks >= 300, 3, status)
    rows = []
    for label, team, x0 in [("Player_1", 2, 0.0), ("Player_2", 3, 500.0)]:
        for t, st in zip(ticks, status):
            rows.append({
                "tick": int(t), "steamid": label, "game_time": t / 64.0, "X": x0 + t, "Y": 0.0, "Z": 0.0,
                "pitch": 1.0, "yaw": 190.0, "health": 100.0, "team_num": float(team), "is_alive": True,
                "active_weapon_name": "AK-47", "active_weapon_ammo": 30.0, "aim_punch_angle": np.array([0.5, -0.25, 0.0]),
                "flash_duration": 0.0, "flash_max_alpha": 255.0, "is_scoped": False, "duck_amount": 0.0,
                "ducking": False, "is_walking": False, "is_airborne": False, "shots_fired": 0.0, "buttons": 0.0,
                "FIRE": False, "spotted": True,
                "approximate_spotted_by": ["Player_2"] if label == "Player_1" and t > 100 else [],
                "round_win_status": int(st), "round_win_reason": 9 if st else 0,
            })
    d = root / split
    d.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(d / f"{n}.parquet")
    events = {
        "round_prestart": [{"tick": 50}, {"tick": 200}],
        "round_freeze_end": [{"tick": 80}, {"tick": 230}],
        "weapon_fire": [{"tick": 120, "user_steamid": "Player_1", "weapon": "weapon_ak47", "silenced": False}],
        "player_hurt": [{"tick": 121, "user_steamid": "Player_2", "attacker_steamid": "Player_1", "weapon": "ak47",
                         "hitgroup": "head", "dmg_health": 100, "dmg_armor": 0, "health": 0, "armor": 0}],
        "player_death": [{"tick": 121, "user_steamid": "Player_2", "attacker_steamid": "Player_1", "assister_steamid": "",
                          "weapon": "ak47", "headshot": True, "hitgroup": "head"}],
        "rank_update": [{"tick": 390, "user_steamid": "Player_1", "rank_old": 1, "rank_new": 2, "rank_type_id": 11,
                         "num_wins": 3, "rank_change": 1.0}],
        "cheaters": [{"steamid": "Player_2"}],
        "CSstats_info": [{"map": "de_mirage", "server": "eu", "avg_rank": "9000",
                          "match_making_type": "Premier Matchmaking"}],
    }
    (d / f"{n}.json").write_text(json.dumps(events))
    return d / f"{n}.parquet"


def test_parse_dataset_match(tmp_path):
    path = write_match(tmp_path)
    assert backend_for_path(path) == "cs2cd"
    demo = get_parser("cs2cd").parse(path)
    p1, p2 = cs2cd_steam_id("with_cheater_present", 7, "Player_1"), cs2cd_steam_id("with_cheater_present", 7, "Player_2")
    assert p1 == 900_020_000_701 and p2 == 900_020_000_702
    assert demo.meta.match_id == "cs2cd-with_cheater_present-7"
    assert demo.meta.map_name == "de_mirage" and demo.meta.mode == "premier"
    assert demo.meta.extra["cheater_steam_ids"] == [p2]
    assert demo.players["steam_id"].tolist() == [p1, p2]

    r = demo.rounds
    assert r["round_number"].tolist() == [1, 2]
    assert r["end_tick"].tolist() == [150, 300]
    assert r["winner"].tolist() == [2, 3]
    assert r["live"].all()

    t = demo.ticks.set_index(["steam_id", "tick"])
    assert t.loc[(p1, 20), "round"] == 0 and not t.loc[(p1, 20), "round_live"]
    assert t.loc[(p1, 100), "round"] == 1 and t.loc[(p1, 100), "round_live"]
    assert t.loc[(p1, 101), "spotted_by"] == [p2]
    assert t.loc[(p1, 101), "ammo"] == 30
    assert t.loc[(p1, 101), "aim_punch_pitch"] == 0.5

    assert demo.event("shots")["steam_id"].tolist() == [p1]
    d = demo.event("deaths").iloc[0]
    assert (d.attacker_steam_id, d.victim_steam_id, d.assister_steam_id) == (p1, p2, 0)
    assert "bullets" not in demo.events


def test_labels_and_index(tmp_path):
    path = write_match(tmp_path)
    lab = match_labels(path.with_suffix(".json"))
    assert lab["cheater_labels"] == ["Player_2"]
    assert lab["cheater_steam_ids"] == [cs2cd_steam_id("with_cheater_present", 7, 2)]
    idx = load_index()
    assert len(idx) == 795
    assert sum(r["split"] == "with_cheater_present" for r in idx) == 317
    assert all(r["cheaters"] for r in idx if r["split"] == "with_cheater_present")


def test_pipeline_keeps_dataset_files(tmp_path, config):
    from cs2_analyzer.pipeline import analyze_demo

    path = write_match(tmp_path)
    res = analyze_demo(path, config, keep_demo=False)
    assert path.exists() and not res.demo_deleted
    assert (res.output_dir / "match.json").exists()


@pytest.mark.skipif(not os.environ.get("CS2A_TEST_CS2CD"), reason="set CS2A_TEST_CS2CD to a dataset .parquet")
def test_real_dataset_match():
    demo = get_parser("cs2cd").parse(Path(os.environ["CS2A_TEST_CS2CD"]))
    assert len(demo.players) == 10
    assert demo.rounds["live"].sum() >= 10
    assert demo.meta.tickrate == 64


def test_normalize_tolerates_missing_optional_props(tmp_path):
    """Some demos (e.g. recent pro demos) lack props such as buttons."""
    from cs2_analyzer.parser.normalize import normalize_ticks

    raw = pd.DataFrame({"tick": [1, 2], "game_time": [0.0, 1 / 64], "steamid": [7, 7], "team_num": [2, 2],
                        "X": [0.0, 1.0], "Y": [0.0, 0.0], "Z": [0.0, 0.0], "pitch": [0.0, 0.0], "yaw": [10.0, 10.0]})
    t = normalize_ticks(raw, pd.DataFrame(), 64.0)
    assert (t["buttons"] == 0).all() and not t["attack"].any()
    assert "buttons" in t.attrs["missing_props"] and "FIRE" in t.attrs["missing_props"]


def test_score_round_ends_ignores_halftime_swap():
    """Tournament demos: round ends come from one team's score going up by one."""
    from cs2_analyzer.parser.demoparser2_backend import DemoParser2Backend

    # (tick, T score, CT score); at tick 50 the sides swap (2-1 becomes 1-2), which is not a round end
    timeline = [(10, 0, 0), (20, 1, 0), (30, 2, 0), (40, 2, 1), (50, 1, 2), (70, 2, 2)]
    rows = []
    for tick, t, ct in timeline:
        rows.append({"tick": tick, "team_num": 2, "team_rounds_total": t})
        rows.append({"tick": tick, "team_num": 3, "team_rounds_total": ct})
    ends = DemoParser2Backend._score_round_ends(pd.DataFrame(rows))
    assert [(e["tick"], e["winner"]) for e in ends] == [(20, 2), (30, 2), (40, 3), (70, 2)]
    assert DemoParser2Backend._score_round_ends(pd.DataFrame({"tick": [1], "team_num": [2]})) == []
