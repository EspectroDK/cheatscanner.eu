#!/usr/bin/env bash
# Runs on the server after GitHub Actions has copied a new version to /srv/cheatscanner.
# Pauses the analysis workers, builds the images, waits (at most 10 minutes) for the demos still being
# analyzed, then restarts what changed and lets the workers go on.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  umask 077
  # 32 random bytes, URL-safe base64. CS2A_SECRET_KEY keeps the trailing "=" (a Fernet key needs it);
  # the others drop it so they are safe inside the database URL.
  secret() { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n'; }
  while IFS= read -r line; do
    if [[ "$line" == CS2A_SECRET_KEY=GENERATE ]]; then echo "${line%GENERATE}$(secret)"
    elif [[ "$line" == *=GENERATE ]]; then echo "${line%GENERATE}$(secret | tr -d =)"
    else echo "$line"; fi
  done < .env.production.example > .env
  echo "Created /srv/cheatscanner/.env with new random secrets."
  echo "Fill in the empty values (ssh deploy@<server>, then: nano /srv/cheatscanner/.env) and run the deploy again."
  exit 1
fi

# Read one value from .env without sourcing it (passwords may contain characters bash would interpret).
envget() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"; }
missing=()
for v in CS2A_STEAM_API_KEY CS2A_CONTACT_EMAIL CS2A_ADMIN_STEAM_IDS STEAM_BOT_USERNAME STEAM_BOT_PASSWORD BORG_REPO; do
  [ -n "$(envget "$v")" ] || missing+=("$v")
done
[ ${#missing[@]} -eq 0 ] || echo "Note: still empty in .env: ${missing[*]}"

mkdir -p data/maps/render data/work/uploads backups
# Restarting a worker hands its demo back to the queue, and that analysis starts over. So first pause the
# workers: they finish the demo they have but take no new one (worker.py), while the images build. The
# new workers start paused and carry on once the deploy lifts the pause; the trap lifts it too if the
# deploy stops early, and workers ignore a pause older than 30 minutes. (A server still running a version
# without `pause` just doesn't pause this once.)
pause() {  # the API container may still be starting right after a restart, so try a few times
  for _ in 1 2 3 4 5 6; do docker compose exec -T api cs2-analyzer pause "$1" >/dev/null 2>&1 && return; sleep 5; done
  echo "Note: could not set the worker pause to $1."
}
pause on
trap 'pause off' EXIT

# Refresh the base images (security fixes) at most once a week; Docker Hub limits anonymous pulls.
if [ -z "$(find backups/.last-pull -mtime -7 2>/dev/null)" ]; then
  docker compose build --pull && docker compose pull --ignore-buildable && touch backups/.last-pull
else
  docker compose build
fi

# Wait (at most 10 minutes) for the demos being analyzed right now; queued ones are safe in the database.
# (.active-jobs is the count kept by API versions from before the queue moved to the database.)
analyzing() {
  docker compose exec -T api cs2-analyzer queue --field processing 2>/dev/null \
    || cat data/work/uploads/.active-jobs 2>/dev/null || echo 0
}
for _ in $(seq 60); do
  busy=$(analyzing | tr -dc 0-9)
  [ "${busy:-0}" -eq 0 ] 2>/dev/null && break
  echo "waiting for $busy demo(s) being analyzed..."
  sleep 10
done

docker compose up -d --remove-orphans
pause off
docker image prune -f >/dev/null

# Map screenshots for the website: not in the repository (Valve's images), so download the missing ones.
docker compose exec -T api cs2-analyzer map-images || echo "Note: some map screenshots could not be downloaded; the site draws those banners."

# Nightly backup (idempotent).
job="17 3 * * * $PWD/deploy/backup.sh >> $PWD/backups/backup.log 2>&1"
(crontab -l 2>/dev/null | grep -v 'deploy/backup.sh' || true; echo "$job") | crontab -

echo "Deployed $(cat .deployed-commit 2>/dev/null || echo 'unknown commit')."
