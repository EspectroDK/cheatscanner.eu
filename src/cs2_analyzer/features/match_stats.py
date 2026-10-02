"""Ordinary scoreboard statistics.

These are stored for context only. They are deliberately NOT inputs to any
detector or to the evidence score: high K/D or headshot rate is what good
legitimate players produce too.
"""

from __future__ import annotations

import pandas as pd

from cs2_analyzer.parser.base import ParsedDemo


def compute_match_player_stats(demo: ParsedDemo) -> pd.DataFrame:
    players = demo.players.copy()
    deaths = demo.event("deaths")
    hurts = demo.event("hurts")
    live_rounds = demo.rounds[demo.rounds["live"]]

    def in_live(df: pd.DataFrame) -> pd.DataFrame:
        if not len(df) or not len(live_rounds):
            return df
        mask = pd.Series(False, index=df.index)
        for r in live_rounds.itertuples():
            mask |= (df["tick"] >= r.freeze_end_tick) & (df["tick"] <= r.end_tick + 64 * 5)
        return df[mask]

    deaths = in_live(deaths)
    hurts = in_live(hurts)
    # Teams swap sides at halftime, so a player's team is identified by the side they STARTED on
    # (first live-round tick on T or CT). Their final side would split teams and, for anyone who
    # left early, be "unassigned".
    on_side = demo.ticks[demo.ticks["team"].isin([2, 3])] if len(demo.ticks) else demo.ticks
    first_side = on_side.sort_values("tick").groupby("steam_id")["team"].first().to_dict() if len(on_side) else {}
    live_side = in_live(on_side) if len(on_side) else on_side
    start_side = live_side.sort_values("tick").groupby("steam_id")["team"].first().to_dict() if len(live_side) else {}
    rows = []
    for p in players.itertuples():
        sid = p.steam_id
        if len(deaths):
            own_kills = deaths[(deaths["attacker_steam_id"] == sid) & (deaths["victim_steam_id"] != sid)]
            kills = len(own_kills)
            hs = int(own_kills["headshot"].sum())
            d = int((deaths["victim_steam_id"] == sid).sum())
            a = int((deaths["assister_steam_id"] == sid).sum())
        else:
            kills = hs = d = a = 0
        dmg = int(hurts.loc[hurts["attacker_steam_id"] == sid, "dmg_health"].clip(upper=100).sum()) if len(hurts) else 0
        rank = None
        if demo.ranks is not None and len(demo.ranks):
            r = demo.ranks[demo.ranks["steam_id"] == sid]
            rank = r.iloc[0] if len(r) else None
        rows.append(
            {
                "steam_id": int(sid),
                "name": p.name,
                "team": int(start_side.get(sid, first_side.get(sid, p.start_team))),
                "kills": kills,
                "deaths": d,
                "assists": a,
                "headshots": hs,
                "damage": dmg,
                "score": None,  # the in-game score prop is not parsed; left missing rather than approximated
                "rank_type": int(rank["rank_type_id"]) if rank is not None and pd.notna(rank["rank_type_id"]) else None,
                "rank_old": int(rank["rank_old"]) if rank is not None and pd.notna(rank["rank_old"]) else None,
                "rank_new": int(rank["rank_new"]) if rank is not None and pd.notna(rank["rank_new"]) else None,
            }
        )
    return pd.DataFrame(rows)


def starting_t_sides(world, match_stats: pd.DataFrame, rounds: pd.DataFrame) -> dict[int, int]:
    """Side (2 = T, 3 = CT) the team that started as T played in each round.

    Read from the players' actual team at freeze end, so halftime and overtime side swaps
    need no rules. Rounds where it can't be read are left out.
    """
    members = [world.index_of[int(sid)] for sid in match_stats.loc[match_stats["team"] == 2, "steam_id"]
               if int(sid) in world.index_of]
    out = {}
    if not members:
        return out
    for r in rounds.itertuples():
        tick = getattr(r, "freeze_end_tick", None)
        if tick is None or pd.isna(tick):
            continue
        t = min(max(world.t(int(tick)), 0), world.team.shape[1] - 1)
        sides = [int(world.team[p, t]) for p in members if int(world.team[p, t]) in (2, 3)]
        if sides:
            out[int(r.round_number)] = max(set(sides), key=sides.count)
    return out
