"""Detector interface, evidence event model and analysis context.

Separation of concerns (design principle 8):

* **raw observations** - every measured instance (e.g. every snap, every
  trigger time) is recorded via ``ctx.observe(...)`` regardless of whether it
  looks suspicious. They feed calibration, baselines and player fingerprints.
* **detector evidence** - :class:`EvidenceEvent` objects, emitted only when a
  detector's (configurable, possibly UNCALIBRATED) criteria are met.
* **match aggregation / player history** - done in ``scoring/``, never here.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from cs2_analyzer import DETECTOR_VERSION
from cs2_analyzer.config import Config


class EvidenceAxis(str, Enum):
    AIM_MECHANICS = "AIM_MECHANICS"
    HIDDEN_INFORMATION = "HIDDEN_INFORMATION"
    SHOT_TIMING = "SHOT_TIMING"
    RECOIL = "RECOIL"
    IMPOSSIBLE_MECHANICS = "IMPOSSIBLE_MECHANICS"
    DECISION_INFORMATION = "DECISION_INFORMATION"


# Evidence groups describe *which underlying anomaly* a detection measures.
# Detections in the same group that overlap in time/target describe the same
# incident and must not be counted twice (see scoring/aggregate.py).
class EvidenceGroup(str, Enum):
    INFORMATION = "information"  # hidden tracking, pre-aim convergence, smoke/flash tracking, remembered position
    AIM = "aim"  # acquisition, snaps, attraction, target switching
    TIMING = "timing"
    RECOIL = "recoil"
    IMPOSSIBLE = "impossible"
    STRATEGIC = "strategic"


@dataclass
class EvidenceEvent:
    match_id: str
    steam_id: int
    round_number: int | None
    tick_start: int
    tick_peak: int
    tick_end: int
    detector_type: str
    severity: float
    reliability: float
    information_confidence: float
    evidence_axis: str
    evidence_group: str
    target_steam_id: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    detector_version: str = DETECTOR_VERSION
    video_path: str | None = None
    debug_plot_path: str | None = None
    id: str = ""

    def __post_init__(self):
        self.severity = float(np.clip(self.severity, 0.0, 1.0))
        self.reliability = float(np.clip(self.reliability, 0.0, 1.0))
        self.information_confidence = float(np.clip(self.information_confidence, 0.0, 1.0))
        if not self.id:
            raw = f"{self.match_id}|{self.steam_id}|{self.detector_type}|{self.tick_start}|{self.tick_peak}|{self.target_steam_id}"
            self.id = hashlib.sha1(raw.encode()).hexdigest()[:24]

    @property
    def confidence(self) -> float:
        """Weight of this event as evidence: severity x reliability x information confidence."""
        return self.severity * self.reliability * self.information_confidence

    @property
    def incident_key(self) -> str:
        return f"{self.steam_id}:{self.target_steam_id}:{self.round_number}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = self.confidence
        d["incident_key"] = self.incident_key
        return _jsonable(d)


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return None if not np.isfinite(v) else round(v, 6)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.ndarray):
        return [_jsonable(v) for v in x.tolist()]
    return x


def jsonable(x):
    return _jsonable(x)


def ramp(x: float, lo: float, hi: float) -> float:
    """Linear 0..1 ramp; used to turn a metric into a severity component."""
    if x is None or not np.isfinite(x):
        return 0.0
    if hi == lo:
        return float(x >= hi)
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


class ObservationSink:
    """Collects raw per-instance measurements from all detectors."""

    def __init__(self):
        self._rows: dict[str, list[dict]] = {}

    def record(self, detector: str, steam_id: int, **values):
        row = {"steam_id": int(steam_id)}
        row.update(values)
        self._rows.setdefault(detector, []).append(row)

    def frame(self, detector: str) -> pd.DataFrame:
        return pd.DataFrame(self._rows.get(detector, []))

    def detectors(self) -> list[str]:
        return list(self._rows)


@dataclass
class AnalysisContext:
    match_id: str
    map_name: str
    demo: Any  # ParsedDemo
    world: Any  # World
    vis: Any  # VisibilityEngine
    knowledge: Any  # KnowledgeModel
    encounters: list
    config: Config
    baselines: Any = None  # BaselineStore | None
    player_filter: set[int] | None = None
    observations: ObservationSink = field(default_factory=ObservationSink)

    def cfg(self, detector: str) -> dict:
        return self.config.section(f"detectors.{detector}")

    def analyze_player(self, p: int) -> bool:
        if self.player_filter is None:
            return True
        return int(self.world.steam_ids[p]) in self.player_filter

    def sid(self, p: int) -> int:
        return int(self.world.steam_ids[p])

    def tick(self, t: int) -> int:
        return self.world.tick(t)

    def observe(self, detector: str, p: int, **values):
        self.observations.record(detector, self.sid(p), **values)


class Detector(ABC):
    """Shared detector interface.

    Subclasses document (in their module docstring) why they exist and which
    false-positive situations they must handle.
    """

    name: str = "base"
    axis: EvidenceAxis = EvidenceAxis.AIM_MECHANICS
    group: EvidenceGroup = EvidenceGroup.AIM
    default_reliability: float = 0.5

    @abstractmethod
    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        ...

    def reliability(self, ctx: AnalysisContext) -> float:
        return float(ctx.cfg(self.name).get("reliability", self.default_reliability))

    def event(self, ctx: AnalysisContext, p: int, t_start: int, t_peak: int, t_end: int, severity: float,
              information_confidence: float, target: int | None, metrics: dict, context: dict,
              explanation: str) -> EvidenceEvent:
        w = ctx.world
        return EvidenceEvent(
            match_id=ctx.match_id,
            steam_id=ctx.sid(p),
            round_number=int(w.round_of[t_peak]),
            tick_start=w.tick(t_start),
            tick_peak=w.tick(t_peak),
            tick_end=w.tick(t_end),
            detector_type=self.name,
            severity=severity,
            reliability=self.reliability(ctx),
            information_confidence=information_confidence,
            evidence_axis=self.axis.value,
            evidence_group=self.group.value,
            target_steam_id=None if target is None else ctx.sid(target),
            metrics=jsonable(metrics),
            context=jsonable(context),
            explanation=explanation,
        )
