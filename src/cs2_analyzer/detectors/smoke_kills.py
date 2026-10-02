"""Detector 23 - kills through smoke, counted over the match.

**Why.** A player with a wallhack sees enemies inside and behind smokes and
shoots them there. The tracking detectors (``smoke_tracking``) only count
moments with no modelled legitimate information, and in a smoke fight there
almost always is some (a recent sighting, footsteps, a teammate's view), so
they rarely get to measure anything. The kills themselves remain: the game
marks every kill whose bullet passed through smoke (``player_death.thrusmoke``).

**What is measured.** Per player the number of kills through smoke and their
share of all kills (team kills and suicides excluded).

**False-positive controls.**

* One lucky spray into a smoke is common; evidence needs many such kills
  (``min_smoke_kills``) that are also a large share of the player's kills
  (``min_share``), so a high-fragging player who sprays a lot does not reach it.
* Calibrated on the 795 CS2CD matches (all maps): the clean players' 99th
  percentile is 4 kills through smoke. 10 or more that are at least 35% of the
  player's kills: 2 of 4,598 clean players (one of them a rage cheater with
  anti-aim, see ``docs/validation/cs2cd/anti_aim_clean_players.txt``) and 3 of
  1,040 labelled cheaters. Rare, but specific.
* Match-scope event: it summarises the whole match, so it is not one
  incident among others.
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp


class SmokeKillsDetector(Detector):
    name = "smoke_kills"
    axis = EvidenceAxis.HIDDEN_INFORMATION
    group = EvidenceGroup.INFORMATION
    default_reliability = 0.5

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        deaths = ctx.demo.event("deaths")
        if deaths is None or not len(deaths) or "thrusmoke" not in deaths:
            return []
        min_kills = int(cfg.get("min_smoke_kills", 10))
        full_kills = int(cfg.get("full_smoke_kills", 18))
        min_share = float(cfg.get("min_share", 0.35))
        att = deaths["attacker_steam_id"].astype("int64").to_numpy()
        vic = deaths["victim_steam_id"].astype("int64").to_numpy()
        smoke = deaths["thrusmoke"].astype(bool).to_numpy()
        ticks = deaths["tick"].astype("int64").to_numpy()
        events = []
        v = np.array([w.index_of.get(int(x), -1) for x in vic], dtype=int)
        kt = np.clip(ticks - w.tick0, 0, w.T - 1)
        for p in range(w.P):
            if not ctx.analyze_player(p):
                continue
            mine = (att == ctx.sid(p)) & (v >= 0) & (v != p)
            for i in np.nonzero(mine)[0]:
                mine[i] = bool(w.is_enemy(p, int(v[i]), int(kt[i])))
            kills = int(mine.sum())
            if not kills:
                continue
            k_smoke = int((mine & smoke).sum())
            share = k_smoke / kills
            ctx.observe(self.name, p, kills=kills, smoke_kills=k_smoke, smoke_kill_share=share)
            if k_smoke < min_kills or share < min_share:
                continue
            t = np.sort(kt[mine & smoke])
            sev = 0.3 + 0.7 * ramp(k_smoke, min_kills, full_kills)
            events.append(self.event(
                ctx, p, int(t[0]), int(t[len(t) // 2]), int(t[-1]), sev, 1.0, None,
                {"kills": kills, "smoke_kills": k_smoke, "smoke_kill_share": share}, {"scope": "match"},
                f"{k_smoke} of {kills} kills ({share * 100:.0f}%) went through smoke according to the game's own kill "
                f"record. Clean players rarely have more than 4 in a match."))
        return events
