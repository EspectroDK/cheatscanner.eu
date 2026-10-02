import pandas as pd

from cs2_analyzer.features.match_stats import compute_match_player_stats
from cs2_analyzer.parser.base import MatchMeta, ParsedDemo


def test_team_is_the_side_a_player_started_on():
    # Warmup on the "wrong" side, first half T, second half CT; player 2 leaves (team 0) after round 1.
    ticks = pd.DataFrame(
        [(1, 0, 3), (1, 100, 2), (1, 300, 3), (2, 100, 2), (2, 150, 0), (3, 100, 3), (3, 300, 2)],
        columns=["steam_id", "tick", "team"])
    rounds = pd.DataFrame({"round_number": [1, 2], "start_tick": [90, 290], "freeze_end_tick": [95, 295],
                           "end_tick": [200, 400], "live": [True, True]})
    players = pd.DataFrame({"steam_id": [1, 2, 3], "name": ["a", "b", "c"], "start_team": [0, 0, 0]})
    demo = ParsedDemo(meta=MatchMeta.__new__(MatchMeta), players=players, rounds=rounds, ticks=ticks, events={})
    teams = compute_match_player_stats(demo).set_index("steam_id")["team"].to_dict()
    assert teams == {1: 2, 2: 2, 3: 3}


def test_score_is_counted_per_starting_team_across_side_swaps():
    from types import SimpleNamespace as R

    import numpy as np

    from cs2_analyzer.features.match_stats import starting_t_sides
    from cs2_analyzer.storage.repository import _score, _winner_team

    # Team that started T plays T in round 1, CT in round 2 (halftime), T again in round 3 (overtime).
    world = R(index_of={1: 0, 2: 1}, team=np.array([[2, 3, 2], [3, 2, 3]]), t=lambda tick: tick // 100)
    stats = pd.DataFrame({"steam_id": [1, 2], "team": [2, 3]})
    rounds = pd.DataFrame({"round_number": [1, 2, 3], "freeze_end_tick": [10, 110, 210]})
    sides = starting_t_sides(world, stats, rounds)
    assert sides == {1: 2, 2: 3, 3: 2}

    meta = {"starting_t_sides": {str(k): v for k, v in sides.items()}}
    played = [R(round_number=1, winner=2), R(round_number=2, winner=2), R(round_number=3, winner=3)]
    assert [_winner_team(r, meta) for r in played] == [2, 3, 3]
    assert _score(played, meta) == {"2": 1, "3": 2}
    # Without stored sides, regulation halves are still known but overtime is not.
    assert _winner_team(R(round_number=13, winner=2), None) == 3
    assert _score(played + [R(round_number=25, winner=2)], None) is None
