"""Detector 26 - scripted bunnyhopping, counted over the match.

**Why.** A bunnyhop script presses jump on the exact tick the player lands, so the
player keeps the speed of the previous jump instead of losing it on the ground.
A human can do this now and then with the scroll wheel; a script does it every
time, for whole runs across the map. Cheat packages ship it next to wallhack and
aim assistance, so it is a cheap tell that is independent of everything the
information and aim detectors look at.

**What is measured.** Per player, from the game's own airborne flag: jumps
(airborne stretches of a plausible jump length), *rehops* (a jump that takes
off again within ``rehop_max_ms`` of landing), *perfect rehops* (take-off
within ``perfect_max_ticks`` ground ticks of landing) and *chains* of at least
``chain_min_hops`` perfect rehops in a row.

**False-positive controls.**

* Both the count and the share matter: a player who rehops a lot but times most
  of them a few ticks late is a human bunnyhopper. Scripts land near 100%.
* Chains are required too: a few perfect rehops spread over a match are luck.
* 60 CS2CD matches (2026-10-07, docs/validation/cs2cd/bunnyhop.md): clean players'
  median is 1 perfect rehop and a 6% share; at 50 perfect rehops, 80% and 20 chains
  the rule fires for 0 of 300 clean players, 1 of 195 unlabelled players (most
  likely an unlabelled cheater) and 17 of 105 labelled cheaters; 0 of 40 players
  on four matchmaking demos. Air speed is not used: CS2 caps it for everyone.
* Match-scope event: it summarises the whole match.
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp


class BunnyhopDetector(Detector):
    name = "bunnyhop"
    axis = EvidenceAxis.IMPOSSIBLE_MECHANICS
    group = EvidenceGroup.IMPOSSIBLE
    default_reliability = 0.7
    label = "Scripted bunnyhopping"

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        if getattr(w, "airborne", None) is None:
            return []
        arc_min = w.ticks_for_ms(cfg.get("arc_min_ms", 300))
        arc_max = w.ticks_for_ms(cfg.get("arc_max_ms", 1100))
        rehop_max = w.ticks_for_ms(cfg.get("rehop_max_ms", 500))
        perfect_max = int(cfg.get("perfect_max_ticks", 2))
        chain_min = int(cfg.get("chain_min_hops", 3))
        min_perfect = int(cfg.get("min_perfect_rehops", 50))
        full_perfect = int(cfg.get("full_perfect_rehops", 150))
        min_share = float(cfg.get("min_perfect_share", 0.8))
        min_chains = int(cfg.get("min_chains", 20))
        full_chains = int(cfg.get("full_chains", 40))
        events = []
        for p in range(w.P):
            if not ctx.analyze_player(p):
                continue
            air = w.airborne[p].astype(bool) & w.alive[p] & w.live
            d = np.diff(np.r_[0, air.astype(np.int8), 0])
            jumps = [(int(a), int(b)) for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)) if arc_min <= b - a <= arc_max]
            gaps = [jumps[i + 1][0] - jumps[i][1] for i in range(len(jumps) - 1)]  # ground ticks landing -> next take-off
            rehops = [g for g in gaps if g <= rehop_max]
            perfect = sum(1 for g in rehops if g <= perfect_max)
            share = perfect / len(rehops) if rehops else 0.0
            chains: list[tuple[int, int]] = []  # (tick of first take-off, hops)
            start, hops = 0, 1
            for i, g in enumerate(gaps):
                if g <= perfect_max:
                    hops += 1
                else:
                    if hops >= chain_min:
                        chains.append((jumps[start][0], hops))
                    start, hops = i + 1, 1
            if hops >= chain_min and jumps:
                chains.append((jumps[start][0], hops))
            longest = max((h for _, h in chains), default=1)
            ctx.observe(self.name, p, jumps=len(jumps), rehops=len(rehops), perfect_rehops=perfect, perfect_share=share,
                        chains=len(chains), longest_chain=longest)
            if perfect < min_perfect or share < min_share or len(chains) < min_chains:
                continue
            sev = min(1.0, 0.4 + 0.3 * ramp(perfect, min_perfect, full_perfect) + 0.3 * ramp(len(chains), min_chains, full_chains))
            t = [c for c, _ in chains]
            events.append(self.event(
                ctx, p, t[0], t[len(t) // 2], t[-1], sev, 1.0, None,
                {"jumps": len(jumps), "rehops": len(rehops), "perfect_rehops": perfect, "perfect_share": round(share, 3),
                 "chains": len(chains), "longest_chain": longest},
                {"scope": "match", "chains": [{"tick": w.tick(c), "hops": h} for c, h in chains[:20]]},
                f"{self.label}: {perfect} of {len(rehops)} rehops ({share * 100:.0f}%) took off within {perfect_max} ticks of "
                f"landing, in {len(chains)} chains of {chain_min} or more (longest {longest}). Clean players manage a handful "
                f"of such jumps per match; a script does it every time."))
        return events
