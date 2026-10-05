"""Detector registry. Order is the order detectors run in."""

from cs2_analyzer.detectors.aim_mechanics import (
    AimAcquisitionDetector,
    AttractionDetector,
    SnapDetector,
    TargetSwitchDetector,
)
from cs2_analyzer.detectors.base import (
    AnalysisContext,
    Detector,
    EvidenceAxis,
    EvidenceEvent,
    EvidenceGroup,
    ObservationSink,
)
from cs2_analyzer.detectors.context_info import FlashDetector, StrategicInformationDetector
from cs2_analyzer.detectors.hidden_tracking import HiddenTrackingDetector, SmokeTrackingDetector
from cs2_analyzer.detectors.hidden_fire import HiddenFireDetector
from cs2_analyzer.detectors.information_gap import InformationGapDetector
from cs2_analyzer.detectors.input_lattice import InputLatticeDetector
from cs2_analyzer.detectors.previsibility import PrevisibilityDetector
from cs2_analyzer.detectors.remembered_position import RememberedPositionDetector
from cs2_analyzer.detectors.shot_mechanics import (
    MechanicalImpossibilityDetector,
    RecoilDetector,
    TriggerTimingDetector,
)
from cs2_analyzer.detectors.smoke_kills import SmokeKillsDetector
from cs2_analyzer.detectors.view_integrity import ViewIntegrityDetector

ALL_DETECTORS: dict[str, type[Detector]] = {
    d.name: d
    for d in (
        HiddenTrackingDetector,
        RememberedPositionDetector,
        PrevisibilityDetector,
        AimAcquisitionDetector,
        SnapDetector,
        AttractionDetector,
        TargetSwitchDetector,
        TriggerTimingDetector,
        RecoilDetector,
        SmokeTrackingDetector,
        InformationGapDetector,
        HiddenFireDetector,
        FlashDetector,
        StrategicInformationDetector,
        MechanicalImpossibilityDetector,
        ViewIntegrityDetector,
        InputLatticeDetector,
        SmokeKillsDetector,
    )
}


def build_detectors(names: list[str] | None = None, enabled: dict | None = None) -> list[Detector]:
    if names:
        unknown = [n for n in names if n not in ALL_DETECTORS]
        if unknown:
            raise ValueError(f"unknown detectors: {unknown}; available: {sorted(ALL_DETECTORS)}")
        return [ALL_DETECTORS[n]() for n in names]
    enabled = enabled or {}
    return [cls() for n, cls in ALL_DETECTORS.items() if enabled.get(n, {}).get("enabled", True)]


__all__ = [
    "ALL_DETECTORS", "AnalysisContext", "Detector", "EvidenceAxis", "EvidenceEvent", "EvidenceGroup",
    "ObservationSink", "build_detectors",
]
