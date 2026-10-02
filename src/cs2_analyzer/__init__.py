"""CS2 behavioral evidence analyzer.

Produces explainable evidence and risk/anomaly scores from CS2 demos. It never
produces a verdict: see docs/methodology.md for how scores must be read.
"""

__version__ = "0.1.0"

# Bump when detector logic or thresholds change so stored results remain
# attributable to the code that produced them.
DETECTOR_VERSION = "0.1.0-uncalibrated"
SCORING_MODEL_VERSION = "evidence-groups-0.1.0"
