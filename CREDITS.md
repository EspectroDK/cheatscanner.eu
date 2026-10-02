# Credits

Cheatscanner stands on other people's data, research and code. This page lists what shaped it. Each
project keeps its own licence, and a mention here doesn't mean its authors endorse Cheatscanner.

<!-- repo-only -->
The website shows this same file at `/#/credits` (rendered by `web/src/pages/DocPage.tsx`; everything between
the repo-only markers is left out there).
<!-- /repo-only -->

## Data for calibration and verification

- **[CS2CD: Counter-Strike 2 Cheat Detection](https://huggingface.co/datasets/CS2CD/CS2CD.Counter-Strike_2_Cheat_Detection)**
  (CC BY 4.0) is 795 anonymised, pre-parsed matchmaking matches with per-player VAC-ban labels. It is the
  clean and cheater corpus behind every calibration in [docs/validation/cs2cd](docs/validation/cs2cd/README.md),
  and `src/cs2_analyzer/data/` bundles baselines derived from it. It was published with the
  [AntiCheatPT paper](https://arxiv.org/abs/2508.06348), whose notes on label quality we rely on.
- **Professional match demos** (tournament SourceTV recordings, via [HLTV](https://www.hltv.org)) served as
  the legitimate high-skill baseline. They showed which metrics measure skill rather than cheating.
  The demos themselves are not included here.
- **The public Premier demo** in [LaihoE/demoparser](https://github.com/LaihoE/demoparser) (`src/parser/test_demo.dem`)
  was the reference for the parser notes and the visibility validation.

## Anti-cheat research and open-source anti-cheats that inspired detectors

- **Silent-aim "snap-and-return" (`view_integrity`)**: [CS2AC](https://github.com/lucianene/CS2AC)'s SnapReturn
  and silent-aim modules, and the angle-repeat check in [Little Anti-Cheat](https://github.com/J-Tanzanite/Little-Anti-Cheat).
- **Mouse-input lattice (`input_lattice`)**: the sensitivity/GCD rotation checks of the Minecraft anti-cheats
  [Grim](https://github.com/GrimAnticheat/Grim) and [MX-Project](https://github.com/kireikosasha/MX-Project).
- **Scoring the player over many observations (`scoring/player_evidence.py`)**: Valve's VACnet
  ([GDC 2018 talk "Robocalypse Now"](https://www.gdcvault.com/play/1024994/Robocalypse-Now-Using-Deep-Learning)),
  [BotScreen](https://github.com/SoftSec-KAIST/BotScreen) (USENIX Security 2023) and
  [GAN-Aimbots](https://arxiv.org/abs/2205.07060).
- **Keeping skilled players from looking like cheaters (the pro baseline)**:
  [AimDetect](https://www.eecis.udel.edu/~hnw/paper/dsn17a.pdf) (DSN 2017) and
  [HAWK](https://arxiv.org/abs/2409.14830).
- **False-positive controls** (repetition before evidence counts, distance gates, de-duplication, a player
  as their own baseline): common practice in the projects above, plus [SMAC](https://github.com/sourcemod-plugins/smac)
  and [circleguard](https://github.com/circleguard/circleguard).

Also read during the survey: [YAACS](https://arxiv.org/abs/2607.04336), the ESP-toggle simulation
([arXiv 2509.24274](https://arxiv.org/abs/2509.24274)), Yeung & Lui, Yu et al., Laurens et al., Alayed et al.,
Galli et al., and the Kaggle CS:GO cheating dataset.

## Companion app and demo fetching

- **[CS2 Player Fetcher](https://github.com/Poggicek/CS2-Player-Fetcher)** by Poggicek (MIT) showed how to read
  Steam's "recently played with" (coplay) list from the running Steam client. The companion's lobby
  reader (`companion/src/main/steam/coplay.ts`) works the same way.
- **[steam-user](https://github.com/DoctorMcKay/node-steam-user)**,
  **[globaloffensive](https://github.com/DoctorMcKay/node-globaloffensive)** and
  **[steam-totp](https://github.com/DoctorMcKay/node-steam-totp)** by Alex Corn (DoctorMcKay) let the demo
  fetcher ask the Game Coordinator for replay URLs without starting the game.
- **[koffi](https://koffi.dev)** calls the Steam client library from Node.
- **[Electron](https://www.electronjs.org)** and **[electron-builder](https://www.electron.build)** run and
  package the app.
- **Valve's [Game State Integration](https://developer.valvesoftware.com/wiki/Counter-Strike:_Global_Offensive_Game_State_Integration)**
  tells the companion when a match is in warm-up or live, without touching the game's memory.

## Map geometry and images

- **[AtomicBool/cs2-map-parser](https://github.com/AtomicBool/cs2-map-parser)** provides the public Mirage
  collision mesh that `maps-fetch` downloads, and its `.tri` format.
- **[awpy](https://github.com/pnxenopoulos/awpy)** (its `VphysParser`) and
  **[ValveResourceFormat / Source2Viewer](https://github.com/ValveResourceFormat/ValveResourceFormat)**
  build meshes from a local CS2 install (`tools/geometry/build_tris.py`).
- **[Intel Embree](https://www.embree.org)** (via [embreex](https://github.com/trimesh/embreex)) does the
  raycasting.
- **Map screenshots** on the map banners come from
  [neustcs/cs2mapsthumbnails](https://github.com/neustcs/cs2mapsthumbnails). They are in-game images of
  Valve's maps, so they are not in this repository: the server downloads and resizes them
  (`cs2-analyzer map-images`); see [SOURCE.md](web/public/maps/SOURCE.md).

## Libraries

- **[demoparser2](https://github.com/LaihoE/demoparser)** by LaihoE parses every CS2 demo.
- **Python**: NumPy, pandas, PyArrow, SciPy, SQLAlchemy, psycopg, FastAPI, Uvicorn, python-multipart,
  Matplotlib, imageio-ffmpeg and cryptography; pytest and HTTPX for tests.
- **[FFmpeg](https://ffmpeg.org)** (bundled by imageio-ffmpeg) encodes the evidence clips.
- **Web**: React, React Router, Vite, TypeScript, Vitest, and the fonts Inter and Barlow Condensed (via
  Fontsource, SIL Open Font License).
- **Hosting**: [PostgreSQL](https://www.postgresql.org), [Caddy](https://caddyserver.com) (HTTPS),
  [Docker](https://www.docker.com) and [Node.js](https://nodejs.org).

Counter-Strike 2, Steam and VAC are trademarks of Valve Corporation. Cheatscanner is not affiliated with
Valve.
