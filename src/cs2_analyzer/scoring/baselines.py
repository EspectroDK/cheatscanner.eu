"""Population baselines (reference distributions of legitimate play).

Detectors compare observations against these when available; until a
baseline exists for a metric/stratum, detectors fall back to configurable
UNCALIBRATED thresholds and say so in their metrics (``calibrated: false``).

Baselines are built by ``tools/calibration/build_baselines.py`` from
observation Parquet files of matches believed to be legitimate. Strata are
canonical ``key=value;...`` strings, e.g.
``weapon_class=rifle;map=de_mirage;range=mid;scoped=0``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def stratum_key(**parts) -> str:
    return ";".join(f"{k}={parts[k]}" for k in sorted(parts) if parts[k] is not None) or "all"


def range_bucket(distance_u: float | None) -> str:
    if distance_u is None or not np.isfinite(distance_u):
        return "unknown"
    return "close" if distance_u < 600 else ("mid" if distance_u < 1500 else "long")


class BaselineStore:
    def __init__(self, stats: dict[tuple[str, str], dict] | None = None):
        self._stats = stats or {}

    @classmethod
    def from_json(cls, path: str | Path) -> "BaselineStore":
        data = json.loads(Path(path).read_text())
        return cls({(d["metric"], d["stratum"]): d for d in data})

    @classmethod
    def from_db(cls, session) -> "BaselineStore":
        from cs2_analyzer.storage.models import BaselineStat

        rows = session.query(BaselineStat).all()
        return cls({(r.metric, r.stratum): {"n": r.n, "mean": r.mean, "std": r.std, "quantiles": r.quantiles or {}} for r in rows})

    def has(self, metric: str) -> bool:
        return any(m == metric for m, _ in self._stats)

    def get(self, metric: str, stratum: str = "all") -> dict | None:
        return self._stats.get((metric, stratum)) or self._stats.get((metric, "all"))

    def percentile(self, metric: str, value: float, stratum: str = "all") -> float | None:
        """Approximate percentile of ``value`` from stored quantiles (None if no baseline)."""
        st = self.get(metric, stratum)
        if not st or not st.get("quantiles"):
            return None
        qs = sorted((float(k), float(v)) for k, v in st["quantiles"].items())
        ps = np.array([q for q, _ in qs])
        vs = np.array([v for _, v in qs])
        return float(np.interp(value, vs, ps))
