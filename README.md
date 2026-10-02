# Cheatscanner (cheatscanner.eu)

The analyzer, website and companion app behind [cheatscanner.eu](https://cheatscanner.eu). The Python
package and CLI are called `cs2-analyzer`.

Offline analyzer for Counter-Strike 2 `.dem` recordings that looks for **explainable behavioral evidence**
consistent with cheating (hidden-information use, aim/trigger/recoil anomalies) and aggregates it into
per-match and per-player **evidence scores**.

It never produces a verdict. A single spectacular flick, smoke kill, prefire or lucky play is never
sufficient evidence; scores require repeated, independent anomalies, and every event can be traced to
the exact tick range, the information the player could legitimately have had, and the metrics that
made it unusual.

> **Status: early, partly calibrated.** Scoring has been calibrated against the public CS2CD dataset and a
> set of pro matches (see [docs/validation/cs2cd](docs/validation/cs2cd/README.md)); many per-detector
> thresholds are still conservative placeholders. Treat every score as a pointer for manual review. See
> [docs/methodology.md](docs/methodology.md).

- Never touches the running game: works only from demo files, offline.
- Raw demos are deleted after successful analysis (unless `--keep-demo`); compact features,
  evidence JSON, plots and clips are kept.
- The same match (by match id or demo SHA-256) is never processed twice unless `--force`.

## Starting everything (website + automatic match fetching)

Three programs run side by side, each in its own terminal window, **all started from the repository
folder** so they read your `.env` (copy `.env.example` to `.env` and fill it in first):

| Window | Command | What it does |
| --- | --- | --- |
| 1. Website and API | `cs2-analyzer serve` | Serves the website at http://localhost:8000, stores results, and downloads and analyzes each new demo. |
| 2. Poller | `cs2-analyzer ingest poll --loop` | Every 10 minutes asks Steam for each user's new matches (share codes) and puts them in the queue. |
| 3. Demo fetcher | `node services/demo-fetcher/index.js` | Logs in the Steam bot account and asks Valve's Game Coordinator where each queued match's demo is, then tells the API. Never starts the game. |

In PowerShell (one command per line; `&&` doesn't work in Windows PowerShell). If you installed into a
virtual environment, activate it in windows 1 and 2 first (for example `.venv\Scripts\Activate.ps1`):

```powershell
# window 1
cd "C:\path\to\cheatscanner"
cs2-analyzer serve

# window 2
cd "C:\path\to\cheatscanner"
cs2-analyzer ingest poll --loop

# window 3
cd "C:\path\to\cheatscanner"
node services/demo-fetcher/index.js
```

Once, and again after each `git pull` that changes them:

```powershell
python -m pip install -e ".[dev]"
cd web
npm ci
npm run build
cd ..
cd services\demo-fetcher
npm ci
cd ..\..
```

A new match shows up on the website a few minutes after it ends: Steam lists it, the poller queues
it, the fetcher finds the demo, and the API analyzes it. Settings shows each match's progress and when
the poller last checked. If "last checked" doesn't move, window 2 isn't running; if the fetcher logs
`HTTP 503`, window 1 was started outside the repository folder and can't see `CS2A_SERVICE_TOKEN`.
Details: [Website](#website) and [Automatic demo fetching](#automatic-demo-fetching).

## Companion app and overlay

`companion/` is the desktop app and in-game overlay (plain Electron) that shows the evidence class of the
players in your match. It learns who is in the match from Steam's "recently played with" list and CS2's
Game State Integration, and never touches the game process. See [companion/README.md](companion/README.md).

## Public server

cheatscanner.eu runs on one small Hetzner server with the same Docker Compose stack plus Caddy for HTTPS
(`docker-compose.prod.yml`). Merges into `main` deploy themselves via the **deploy** GitHub workflow (it
does nothing until the `DEPLOY_*` repository secrets exist), and map meshes go up with
`tools/deploy/push-maps.ps1`. To run your own: [docs/deploy/hetzner.md](docs/deploy/hetzner.md).

## Quick start (local Python)

```bash
python -m pip install -e ".[dev]"
cs2-analyzer maps-fetch de_mirage              # collision mesh used for line-of-sight raycasting
cs2-analyzer analyze match.dem --debug          # deletes match.dem afterwards; add --keep-demo to keep it
open output/<match_id>/report.html
```

By default results are stored in SQLite (`data/cs2_analyzer.sqlite`); set `CS2A_DATABASE_URL` or
`--db-url` for PostgreSQL.

## Quick start (Docker Compose + PostgreSQL)

```bash
cp .env.example .env
docker compose up -d db
docker compose run --rm analyzer maps-fetch de_mirage
cp /path/to/match.dem demos/
docker compose run --rm analyzer analyze /demos/match.dem --debug --generate-evidence
docker compose up api                           # http://localhost:8000/docs
```

## CLI

```
cs2-analyzer analyze <demo.dem>... [--keep-demo] [--export-parquet] [--generate-evidence]
                                   [--evidence-min-class NORMAL|ELEVATED|HIGH|VERY_HIGH]
                                   [--debug] [--player <steamid>] [--detectors a,b] [--force]
                                   [--match-id ID] [--output DIR] [--observations DIR] [--no-db] [--json]
cs2-analyzer analyze <dataset>/<split>/<n>.parquet ...   # CS2CD dataset match (see below)
cs2-analyzer player <steamid> [--evidence]      # stored history / risk
cs2-analyzer serve [--host --port]              # REST API
cs2-analyzer maps-fetch <map>                   # download a .tri collision mesh
cs2-analyzer inspect-visibility <demo> --observer <sid> --target <sid> --tick <n>
cs2-analyzer db-init
cs2-analyzer calibrate report|build-baselines [--obs-dir DIR] [--exclude FILE]
cs2-analyzer cs2cd list|fetch|labels [--map de_mirage] [--split ...] [--limit N] [--dest DIR]
```

### Accounts and Steam sign-in

The API can require a Steam sign-in. It is off by
default, so local use works as before. Set `CS2A_AUTH_ENABLED=1` and `CS2A_PUBLIC_URL` (the address
the browser uses, e.g. `https://scanner.example`) to turn it on:

- `GET /auth/steam/login` sends the browser to Steam; the callback is verified directly with Steam
  before the SteamID is trusted, then a session cookie is set. `GET /me` shows the signed-in user.
- `POST /me/tokens` (from a signed-in browser) creates an API token for programs, sent as
  `Authorization: Bearer <token>`; `DELETE /me/tokens/{id}` revokes it. Only hashes are stored.
- All data endpoints then need a session or token; `/health` and `/site-info` stay public.
- `CS2A_MAX_UPLOAD_MB` (default 1000) caps website uploads; larger ones get HTTP 413.
- Per signed-in user: one upload at a time (`CS2A_MAX_PARALLEL_UPLOADS`), 10 per 24 hours
  (`CS2A_MAX_UPLOADS_PER_DAY`) and at most 3 uploaded demos waiting for analysis
  (`CS2A_MAX_QUEUED_UPLOADS`); over a limit the upload gets HTTP 429. Automatically fetched matches
  don't count. Below `CS2A_MIN_FREE_DISK_GB` (default 20) free on the server, all uploads get HTTP 507.
- `CS2A_ADMIN_STEAM_IDS` (comma-separated SteamID64s, e.g. your own) may open the Admin page: queue, analysis
  speed, users, linked overlays and totals. Everyone else gets 404 there, and the menu link is hidden.
- `CS2A_CONTACT_EMAIL` is shown on the website's Privacy page as the address for data requests.
- `CS2A_STEAM_API_KEY` ([get one](https://steamcommunity.com/dev/apikey)) is used for names and avatars
  and is needed for the match-history onboarding below.

### Website

The website (`web/`, React + Vite) signs you in with Steam and shows your analyzed matches, the players
in them, their evidence and a demo upload page. With sign-in enabled, a user sees only matches they
played in or uploaded, and only players they have played with or against. For those players it shows
evidence and a per-match timeline from all their analyzed matches; matches the user wasn't in show map,
date and names but can't be opened, and other players there link only if the user knows them. Uploading a
demo counts like playing in that match: the uploader sees every player in it. A Privacy page (`#/privacy`,
readable before sign-in) lists what is stored. Match and player pages play the evidence clips and charts inline (rendered for
each player's strongest events, whatever their overall class, up to `evidence.max_clips_per_match`), with a `demo_gototick` command to
jump to the moment in CS2's own demo player.

```bash
# once; the API then serves it at http://localhost:8000/
cd web
npm install
npm run build
cd ..
# with CS2A_AUTH_ENABLED=1 in .env (or: export CS2A_AUTH_ENABLED=1 / PowerShell: $env:CS2A_AUTH_ENABLED="1")
cs2-analyzer serve
```

For development, run `npm run dev` in `web/` (http://localhost:5173, API calls are forwarded to :8000).
The Docker image builds the website automatically.

### Automatic demo fetching

With sign-in enabled, new users connect their Steam match history before they see any data: they paste
their *game authentication code* and one recent match share code from
[Steam Support](https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128).
Steam checks both, and the code is stored encrypted (`CS2A_SECRET_KEY`, or a key generated in
`data/secret.key`). Users can replace or remove it in Settings. Set `CS2A_REQUIRE_MATCH_ACCESS=0` to
make this step optional.

`cs2-analyzer` and the demo fetcher read settings from `.env` in the folder you start them in (the
fetcher also from the repository root), so the values from `.env.example` work outside Docker too.

From there, three pieces work together:

1. `cs2-analyzer ingest poll --loop` asks Steam every 10 minutes for each user's new share codes and
   queues them (needs `CS2A_STEAM_API_KEY`).
2. [`services/demo-fetcher`](services/demo-fetcher/README.md) logs in a dedicated Steam bot account,
   asks Valve's Game Coordinator for each queued match's replay URL and reports it to the API
   (needs `CS2A_SERVICE_TOKEN`). It never starts the game.
3. The API downloads the demo from Valve's replay server, analyzes it like an upload and deletes it.
   Valve keeps demos only for a few weeks, so older matches end as "no longer available".

In Docker Compose: `docker compose --profile fetch up -d` starts the poller and the demo fetcher next
to the API.

**Steam chat messages (opt-in).** In Settings a user can ask for a Steam chat message when a match
they played in has been analyzed: the map, the number of evidence events, how many players got a
raised evidence class, and the link. The demo fetcher's bot sends them. Steam only lets it message
friends, so the user adds the bot as a friend; the bot accepts requests only from users who switched
the messages on. Each user gets at most one message per match.

### Calibration data: CS2CD

The [CS2CD dataset](https://huggingface.co/datasets/CS2CD/CS2CD.Counter-Strike_2_Cheat_Detection)
(795 anonymised, pre-parsed matches with per-player VAC-ban labels) can be analysed directly:
`cs2-analyzer cs2cd fetch --map de_mirage` downloads matches, and `analyze` reads a `.parquet` file
with its sibling `.json`. Dataset files are never deleted after analysis. Results of the first
Mirage run are in [docs/validation/cs2cd](docs/validation/cs2cd/README.md).

Example output (real Premier demo on de_mirage, abbreviated):

```
Match: sha256-84a1a4191302bdd2a3bbb5a7  (content_hash)
Map: de_mirage   Mode: premier (rank_update.rank_type_id)   Tickrate: 64
Rounds: 10 (9 live)   Encounters: 298
Players analyzed: 10

76561198000000001  player
  K/D/A 11/4/2  (context only, not evidence)
  Evidence score: NORMAL  (0.00)
  Hidden information:     0.00
  Aim anomalies:          0.00
  ...
Evidence written to:
output/sha256-84a1a4191302bdd2a3bbb5a7/
```

Outputs per match (`output/<match_id>/`):

| file | content |
|---|---|
| `match.json` | match/round metadata, versions, data checks, visibility summary, all assessments |
| `<steamid>/evidence.json` | assessment, incidents, every evidence event with metrics/context/explanation, fingerprint |
| `<steamid>/plots/<event>.png` | debug figure: top-down map, collision-mesh POV, error/bearing/kinematics timelines, LOS + knowledge strips |
| `<steamid>/clips/<event>.mp4` | 10 s reviewer clip: suspect POV reconstruction + reviewer-only enemy outline + metrics overlay |
| `report.html` | table of players and their top events with plots/clips |
| `ticks.parquet`, `encounter_channels.parquet` | with `--export-parquet`: normalized ticks and per-tick encounter channels (ML-ready) |

Raw per-detector observations (every snap, trigger time, spray, hidden window...) go to
`data/observations/<match_id>/*.parquet` for calibration.

## REST API

`POST /matches` (multipart `.dem` upload, queued for analysis) · `GET /jobs/{id}` ·
`GET /matches/{id}` · `GET /players/{steamId}` · `GET /players/{steamId}/matches` ·
`GET /players/{steamId}/evidence` · `GET /risk/{steamId}` · `POST /risk/batch` · `GET /health`

Uploads and fetched matches go into an analysis queue in the database. `cs2-analyzer serve` analyzes them
itself (`api.workers` threads, default 1); the public server instead runs separate `cs2-analyzer worker`
processes (any number, `CS2A_WORKERS`) and sets `CS2A_API_WORKERS=0`. `cs2-analyzer queue` shows the queue.

Companion app: `POST /companion/pair` (app gets a link code) · `POST /companion/pair/confirm` (signed-in
browser confirms it) · `POST /companion/pair/token` (app collects its token) · `POST /lobby/risk` (class and
analyzed-match count for the players in the current match).

## Architecture

```
src/cs2_analyzer/
  parser/        parser abstraction (ParsedDemo) + demoparser2 backend + tick normalization
  world.py       dense [player, tick] state reconstruction
  geometry/      angles/vectors, collision mesh + Embree raycasting, smoke model, visibility engine
  knowledge/     legitimate-information model (sight, radar, teammates, sound, damage, objective)
  encounters/    encounter windows + ML-ready per-tick channels
  detectors/     14 explainable detectors sharing one interface (see docs/detectors.md)
  features/      pair kinematics, weapons, scoreboard stats, player fingerprints
  scoring/       incident grouping, axis/family aggregation, history, population baselines, per-player evidence
  evidence/      evidence JSON, debug plots, mesh renderer, MP4 clips, HTML report
  storage/       SQLAlchemy models + repository (PostgreSQL / SQLite)
  api/           FastAPI app
  pipeline.py    orchestration;  cli.py  command line
  worker.py      analysis workers: take queued demos from the database (in the API or `cs2-analyzer worker`)
tools/calibration   calibrate.sh: distribution report + baselines in one step
tools/visualization validate_visibility.py: LOS vs the game's own spotting
docs/               methodology, detectors, geometry, parser notes, validation
```

## Map geometry

Line-of-sight uses the map's physics collision mesh as a `.tri` triangle file in `data/maps/`.
`cs2-analyzer maps-fetch de_mirage` downloads a public Mirage mesh. For other maps, generate one from the
map's `.vphys` with awpy (`awpy generate-tri`) or download awpy's prebuilt tris; see
[docs/geometry.md](docs/geometry.md). Without geometry every visibility state is `UNKNOWN`, so
hidden-information detectors cannot fire (by design).
`cs2-analyzer maps-check [files or folders]` checks meshes (triangle count, readable patch file) before use.

## Licence

Cheatscanner is licensed under the [GNU Affero General Public License v3.0](LICENSE). The public
repository is <https://github.com/EspectroDK/cheatscanner.eu>. If you run a modified version as a website or service, you must offer its users the source of that version.

## Credits

The calibration data, research and open-source projects this builds on are listed in
[CREDITS.md](CREDITS.md).

This project have been developed using AI tools - more specifically Claude, and mainly Opus 5.5. Commits are, however, reviewed by me. 

## Tests

```bash
python -m pytest            # synthetic scenarios, geometry, knowledge, scoring, storage, API
CS2A_TEST_DEMO=/path/to/demo.dem python -m pytest tests/test_real_demo.py   # optional real-demo regression
```
