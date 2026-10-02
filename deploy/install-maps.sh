#!/usr/bin/env bash
# Called by tools/deploy/push-maps.ps1 after it copied new meshes to data/maps/incoming (and incoming/render).
# Checks every file, then moves them into place. Analyses load the mesh per match, so nothing restarts.
set -euo pipefail
cd "$(dirname "$0")/.."
inc=data/maps/incoming
shopt -s nullglob
files=("$inc"/*.tri "$inc"/render/*.tri)
[ ${#files[@]} -gt 0 ] || { echo "nothing in $inc"; exit 1; }

docker compose exec -T api cs2-analyzer maps-check /app/data/maps/incoming || {
  echo "Not installed: fix the files marked BAD and push again."; exit 1; }

mkdir -p data/maps/render
for f in "$inc"/*.tri.json "$inc"/*.tri; do mv -f "$f" data/maps/; echo "installed $(basename "$f")"; done
for f in "$inc"/render/*.tri; do mv -f "$f" data/maps/render/; echo "installed render/$(basename "$f")"; done
echo "Done. The next analysis on these maps uses the new meshes."
