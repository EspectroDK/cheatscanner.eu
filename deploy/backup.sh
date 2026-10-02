#!/usr/bin/env bash
# Nightly backup: database dump + observations + map meshes + .env, into an encrypted borg repository on
# the Storage Box (BORG_REPO). Keeps 14 daily, 8 weekly and 6 monthly archives there, and 7 local dumps.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# Read one value from .env without sourcing it (passwords may contain characters bash would interpret).
envget() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"; }
POSTGRES_USER=$(envget POSTGRES_USER); POSTGRES_DB=$(envget POSTGRES_DB); HEALTHCHECK_URL=$(envget HEALTHCHECK_URL)
BORG_REPO=$(envget BORG_REPO); BORG_PASSPHRASE=$(envget BORG_PASSPHRASE)
export BORG_REPO BORG_PASSPHRASE
export BORG_RSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

ping() { [ -n "${HEALTHCHECK_URL:-}" ] && curl -fsS -m 10 --retry 3 -o /dev/null --data-raw "$2" "$HEALTHCHECK_URL$1" || true; }
fail() { echo "$(date -Is) backup FAILED: $1"; ping /fail "$1"; exit 1; }

mkdir -p backups
dump="backups/db-$(date +%F).dump"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "$dump.tmp" || fail "pg_dump"
mv "$dump.tmp" "$dump"
find backups -maxdepth 1 -name "db-*.dump" | sort -r | tail -n +8 | xargs -r rm --

if [ -n "${BORG_REPO:-}" ]; then
  borg info >/dev/null 2>&1 || borg init --encryption=repokey-blake2 || fail "borg init"
  borg create --compression zstd "::{now:%Y-%m-%d}" "$dump" .env data/observations data/maps \
    --exclude data/maps/incoming || fail "borg create"
  borg prune --keep-daily 14 --keep-weekly 8 --keep-monthly 6 || fail "borg prune"
  borg compact >/dev/null 2>&1 || true
fi

free_gb=$(df -BG --output=avail data | tail -1 | tr -dc 0-9)
[ "$free_gb" -ge 30 ] || fail "only ${free_gb} GB free disk"
echo "$(date -Is) backup ok ($dump, ${free_gb} GB free)"
ping "" "ok, ${free_gb} GB free"
