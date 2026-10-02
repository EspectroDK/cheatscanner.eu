#!/usr/bin/env sh
# Build a calibration report and population baselines from all analysed matches.
# Run only on a corpus believed to be legitimate; pass --exclude <steamid,...> for known/suspected cheaters.
set -eu
OUT=${1:-output/calibration}
shift || true
cs2-analyzer calibrate report --out "$OUT" "$@"
cs2-analyzer calibrate build-baselines --json-out "$OUT/baselines.json" "$@"
echo "report: $OUT/calibration_report.md"
