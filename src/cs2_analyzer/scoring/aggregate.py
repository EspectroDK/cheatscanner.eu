"""Evidence aggregation: events -> player-match assessment -> player history.

Rules (see docs/methodology.md):

1. **Incidents, not events.** Events of the same evidence group, same player,
   same target and overlapping/adjacent tick ranges describe one incident
   (e.g. hidden tracking + pre-visibility convergence + smoke tracking of the
   same enemy in the same moment). An incident counts once: its strongest
   event plus a small, capped agreement bonus. No triple counting.
2. **Repetition required.** An axis score from fewer than
   ``min_incidents`` qualifying incidents is capped at ``single_incident_cap``:
   one spectacular play can never produce a high score. The cap sits just
   below ELEVATED, so a single incident needs other evidence (a second
   family or an unusual player profile) to lift the match.
3. **Axes are not averaged.** Within an axis incidents combine by noisy-OR.
   Axes are grouped into families that are likely to be correlated
   (information vs. mechanics); within a family the strongest axis dominates,
   across families evidence combines by noisy-OR, and a corroboration bonus is
   added only when *independent* families are both elevated.
4. **Player profile.** The per-player evidence accumulated over all of a
   player's observations (scoring/player_evidence.py) is its own family,
   ``profile``. It joins the noisy-OR but never counts as independent
   corroboration, since it re-uses the detectors' measurements.
5. **No probability language.** Output is an evidence score with configurable
   class labels, or INSUFFICIENT_DATA.

All weights and thresholds are UNCALIBRATED placeholders (config ``scoring``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from cs2_analyzer import SCORING_MODEL_VERSION
from cs2_analyzer.detectors.base import EvidenceAxis, EvidenceEvent

AXIS_FIELD = {
    EvidenceAxis.AIM_MECHANICS.value: "aim_score",
    EvidenceAxis.HIDDEN_INFORMATION.value: "hidden_information_score",
    EvidenceAxis.SHOT_TIMING.value: "shot_timing_score",
    EvidenceAxis.RECOIL.value: "recoil_score",
    EvidenceAxis.IMPOSSIBLE_MECHANICS.value: "mechanical_impossibility_score",
    EvidenceAxis.DECISION_INFORMATION.value: "decision_information_score",
}


@dataclass
class Incident:
    key: str
    axis: str
    group: str
    events: list[EvidenceEvent]
    strength: float

    def to_dict(self) -> dict:
        return {
            "incident": self.key,
            "axis": self.axis,
            "group": self.group,
            "strength": round(self.strength, 4),
            "event_ids": [e.id for e in self.events],
            "detectors": sorted({e.detector_type for e in self.events}),
        }


@dataclass
class MatchAssessment:
    steam_id: int
    match_id: str
    axis_scores: dict[str, float]
    overall: float
    classification: str
    evidence_event_count: int
    high_severity_event_count: int
    encounters_analyzed: int
    incidents: list[Incident] = field(default_factory=list)
    family_scores: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    model_version: str = SCORING_MODEL_VERSION
    population: dict | None = None  # scoring/population.py; reported only, never changes the class
    profile: dict | None = None  # scoring/player_evidence.py; enters the score as the "profile" family

    def to_dict(self) -> dict:
        return {
            "steam_id": str(self.steam_id),
            "match_id": self.match_id,
            "classification": self.classification,
            "overall_evidence_score": round(self.overall, 4),
            "axes": {k: round(v, 4) for k, v in self.axis_scores.items()},
            "families": {k: round(v, 4) for k, v in self.family_scores.items()},
            "evidence_event_count": self.evidence_event_count,
            "high_severity_event_count": self.high_severity_event_count,
            "encounters_analyzed": self.encounters_analyzed,
            "incidents": [i.to_dict() for i in self.incidents],
            "notes": self.notes,
            "population_comparison": self.population,
            "player_evidence": self.profile,
            "model_version": self.model_version,
        }


def classify(score: float, cfg: dict, insufficient: bool) -> str:
    if insufficient:
        return cfg.get("insufficient_label", "INSUFFICIENT_DATA")
    labels = cfg.get("labels", ["NORMAL", "ELEVATED", "HIGH", "VERY_HIGH"])
    thresholds = cfg.get("thresholds", [0.25, 0.5, 0.75])
    k = int(np.searchsorted(np.asarray(thresholds), score, side="right"))
    return labels[min(k, len(labels) - 1)]


def build_incidents(events: list[EvidenceEvent], merge_gap_ticks: int, agreement_bonus: float) -> list[Incident]:
    buckets: dict[tuple, list[EvidenceEvent]] = defaultdict(list)
    for ev in events:
        buckets[(ev.evidence_axis, ev.evidence_group, ev.target_steam_id, ev.round_number)].append(ev)
    incidents = []
    for (axis, group, target, rnd), evs in buckets.items():
        evs.sort(key=lambda e: e.tick_start)
        clusters: list[list[EvidenceEvent]] = []
        for ev in evs:
            scoped = ev.context.get("scope") == "match" if isinstance(ev.context, dict) else False
            if clusters and not scoped and ev.tick_start <= max(x.tick_end for x in clusters[-1]) + merge_gap_ticks \
                    and not any(isinstance(x.context, dict) and x.context.get("scope") == "match" for x in clusters[-1]):
                clusters[-1].append(ev)
            else:
                clusters.append([ev])
        for i, cl in enumerate(clusters):
            conf = sorted((e.confidence for e in cl), reverse=True)
            n_det = len({e.detector_type for e in cl})
            strength = conf[0] + (min(agreement_bonus, conf[1] * agreement_bonus) if len(conf) > 1 and n_det > 1 else 0.0)
            incidents.append(Incident(key=f"{axis}:{target}:{rnd}:{i}", axis=axis, group=group, events=cl,
                                      strength=float(min(strength, 1.0))))
    return incidents


def assess_match(steam_id: int, match_id: str, events: list[EvidenceEvent], encounters_analyzed: int,
                 rounds_alive: int, cfg: dict, tickrate: float = 64.0, profile: dict | None = None) -> MatchAssessment:
    merge_gap = int(cfg.get("incident_merge_gap_ms", 3000) / 1000 * tickrate)
    incidents = build_incidents(events, merge_gap, float(cfg.get("agreement_bonus", 0.1)))
    axis_weights = cfg.get("axis_weights", {})
    min_inc = int(cfg.get("min_incidents", 2))
    min_inc_strength = float(cfg.get("min_incident_strength", 0.15))
    cap = float(cfg.get("single_incident_cap", 0.24))
    notes = []

    axis_scores: dict[str, float] = {}
    for axis in AXIS_FIELD:
        inc = [i for i in incidents if i.axis == axis]
        if not inc:
            axis_scores[axis] = 0.0
            continue
        s = 1.0 - float(np.prod([1.0 - i.strength for i in inc]))
        qualifying = sum(1 for i in inc if i.strength >= min_inc_strength)
        match_scoped = any(any(isinstance(e.context, dict) and e.context.get("scope") == "match" for e in i.events) for i in inc)
        # match-scope statistics already aggregate many samples; single incidents do not
        if qualifying < min_inc and not match_scoped and s > cap:
            notes.append(f"{axis}: capped at {cap} (only {qualifying} qualifying incident(s); repetition required)")
            s = cap
        axis_scores[axis] = s * float(axis_weights.get(axis, 1.0))

    families = cfg.get("families", {
        "information": [EvidenceAxis.HIDDEN_INFORMATION.value, EvidenceAxis.DECISION_INFORMATION.value],
        "mechanics": [EvidenceAxis.AIM_MECHANICS.value, EvidenceAxis.SHOT_TIMING.value, EvidenceAxis.RECOIL.value,
                      EvidenceAxis.IMPOSSIBLE_MECHANICS.value],
    })
    within = float(cfg.get("within_family_secondary_weight", 0.25))
    family_scores = {}
    for fam, axes in families.items():
        vals = sorted((axis_scores.get(a, 0.0) for a in axes), reverse=True)
        family_scores[fam] = float(min(1.0, vals[0] + within * sum(vals[1:]))) if vals else 0.0
    elevated = [f for f, v in family_scores.items() if v >= float(cfg.get("corroboration_min_family", 0.3))]
    if profile is not None:
        family_scores["profile"] = float(profile.get("strength", 0.0))
        notes.append(f"player evidence over the match: score {profile['score']:+.1f}, above "
                     f"{profile['clean_percentile'] * 100:.1f}% of clean players (strength {family_scores['profile']:.2f})")
    overall = 1.0 - float(np.prod([1.0 - v for v in family_scores.values()]))
    if len(elevated) >= 2:
        bonus = float(cfg.get("corroboration_bonus", 0.15)) * min(family_scores[f] for f in elevated)
        overall = min(1.0, overall + bonus)
        notes.append(f"independent corroboration across {elevated}: +{bonus:.3f}")

    insufficient = encounters_analyzed < int(cfg.get("min_encounters", 15)) or rounds_alive < int(cfg.get("min_rounds", 5))
    if insufficient:
        notes.append(f"insufficient data: {encounters_analyzed} encounters, {rounds_alive} rounds")
    high_sev = float(cfg.get("high_severity_threshold", 0.7))
    return MatchAssessment(
        steam_id=steam_id,
        match_id=match_id,
        axis_scores=axis_scores,
        overall=overall,
        classification=classify(overall, cfg.get("classification", {}), insufficient),
        evidence_event_count=len(events),
        high_severity_event_count=sum(1 for e in events if e.severity >= high_sev),
        encounters_analyzed=encounters_analyzed,
        incidents=sorted(incidents, key=lambda i: -i.strength),
        family_scores=family_scores,
        notes=notes,
        profile=profile,
    )


def profile_history(rows: list[dict], cfg: dict) -> dict | None:
    """Combine a player's per-match player-evidence percentiles across matches.

    ``rows``: dicts with ``profile`` (the match's ``player_evidence``: clean
    percentile and strength), ``played_at`` (datetime or None) and ``match_id``.

    Each match's clean percentile becomes a z-score (clean players are
    N(0, 1) per match by construction), clipped to [``z_floor``, ``z_cap``]
    so that one extreme match cannot dominate and clean matches cannot cancel
    much. Matches are weighted by age (half-life ``half_life_days``). One
    player's habits repeat across matches, so the weighted mean is compared
    with its variance under a within-player correlation ``rho``:

        Var = (1 - rho) / n_eff + rho,   n_eff = (sum w)^2 / sum w^2

    With rho > 0 a player can never collect a high score from many mildly
    unusual matches; it takes consistently high matches. The per-match
    breakdown and a jump of the latest match over the player's earlier ones
    are reported alongside.
    """
    from statistics import NormalDist

    nd = NormalDist()
    pts = [r for r in rows if isinstance(r.get("profile"), dict) and r["profile"].get("clean_percentile") is not None]
    if len(pts) < int(cfg.get("profile_min_matches", 2)):
        return None
    eps = float(cfg.get("profile_percentile_eps", 0.001))
    lo, hi = float(cfg.get("profile_z_floor", -2.0)), float(cfg.get("profile_z_cap", 3.0))
    rho = float(cfg.get("profile_rho", 0.5))
    half_life = float(cfg.get("profile_half_life_days", 180.0))
    dated = [r["played_at"] for r in pts if r.get("played_at")]
    newest = max(dated) if dated else None
    pts = sorted(pts, key=lambda r: (r.get("played_at") is None, r.get("played_at") or 0))
    z, w, per = [], [], []
    for r in pts:
        pct = min(max(float(r["profile"]["clean_percentile"]), eps), 1 - eps)
        zi = float(np.clip(nd.inv_cdf(pct), lo, hi))
        age = (newest - r["played_at"]).total_seconds() / 86400 if newest and r.get("played_at") else 0.0
        wi = 0.5 ** (age / half_life) if half_life > 0 else 1.0
        z.append(zi)
        w.append(wi)
        per.append({"match_id": r.get("match_id"), "z": round(zi, 3), "weight": round(wi, 3),
                    "clean_percentile": r["profile"]["clean_percentile"]})
    z, w = np.asarray(z), np.asarray(w)
    mean = float(np.sum(w * z) / np.sum(w))
    n_eff = float(np.sum(w) ** 2 / np.sum(w ** 2))
    stat = mean / float(np.sqrt((1 - rho) / n_eff + rho))
    pct = nd.cdf(stat)
    from cs2_analyzer.scoring.player_evidence import DEFAULT_STRENGTH

    sm = cfg.get("profile_strength", DEFAULT_STRENGTH)
    strength = float(np.interp(pct, sm["percentiles"], sm["values"]))
    jump = None
    if len(z) >= int(cfg.get("profile_jump_min_prior", 3)) + 1:
        jump = float(z[-1] - np.sum(w[:-1] * z[:-1]) / np.sum(w[:-1]))
    return {"matches": len(z), "effective_matches": round(n_eff, 2), "mean_z": round(mean, 3), "z": round(stat, 3),
            "percentile": round(pct, 4), "strength": round(strength, 4), "rho": rho,
            "latest_jump_z": None if jump is None else round(jump, 3),
            "sudden_change": jump is not None and jump >= float(cfg.get("profile_jump_z", 2.0)),
            "per_match": per}


def assess_history(match_rows: list[dict], cfg: dict) -> dict:
    """Aggregate a player's per-match assessments without overwriting them.

    ``match_rows``: dicts with keys overall, aim_score, hidden_information_score,
    shot_timing_score, recoil_score, decision_information_score, classification,
    encounters_analyzed, processed_at; optionally ``profile`` (the match's
    player evidence) and ``played_at``.

    Event evidence uses a shrunk mean (``prior_matches`` pseudo-matches of
    score 0) so that a few matches cannot produce a high history score, and so
    that many mildly elevated legitimate matches do not accumulate into a high
    score (which a noisy-OR across matches would do). The per-match profile is
    taken out of each match's overall score first and combined across matches
    by :func:`profile_history` instead, then joined by noisy-OR.
    """
    n = len(match_rows)
    prior = float(cfg.get("prior_matches", 3))
    high_label_min = float(cfg.get("high_match_threshold", 0.5))
    valid = [r for r in match_rows if r["classification"] != "INSUFFICIENT_DATA"]
    nv = len(valid)

    def events_only(r):
        s = float((r.get("profile") or {}).get("strength") or 0.0)
        return r["overall"] if s <= 0 else max(0.0, 1.0 - (1.0 - r["overall"]) / (1.0 - s))

    def shrunk(key):
        return float(sum(r[key] for r in valid) / (nv + prior)) if nv else 0.0

    high = sum(1 for r in valid if r["overall"] >= high_label_min)
    hist = float(sum(events_only(r) for r in valid) / (nv + prior)) if nv else 0.0
    if nv >= int(cfg.get("consistency_min_matches", 3)):
        hist = min(1.0, hist + float(cfg.get("consistency_weight", 0.25)) * (high / nv) * min(1.0, high / 5))
    prof = profile_history(valid, cfg)
    if prof is not None:
        hist = 1.0 - (1.0 - hist) * (1.0 - prof["strength"])
    total_enc = sum(r.get("encounters_analyzed", 0) for r in match_rows)
    if nv == 0 or total_enc < int(cfg.get("min_total_encounters", 30)):
        cls = "INSUFFICIENT_DATA"
    else:
        cls = classify(hist, cfg.get("classification", {}), False)
    conf = "LOW" if nv < 3 else ("MEDIUM" if nv < 10 else "HIGH")
    return {
        "matches_analyzed": n,
        "valid_matches": nv,
        "historical_evidence_score": hist,
        "classification": cls,
        "confidence_level": conf,
        "aim_score": shrunk("aim_score"),
        "information_score": max(shrunk("hidden_information_score"), shrunk("decision_information_score")),
        "trigger_score": shrunk("shot_timing_score"),
        "recoil_score": shrunk("recoil_score"),
        "high_severity_matches": high,
        "player_evidence_history": prof,
        "model_version": SCORING_MODEL_VERSION,
    }
