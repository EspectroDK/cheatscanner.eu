# Cheatscanner app

The desktop companion and in-game overlay for [cheatscanner.eu](https://cheatscanner.eu). When a CS2
match loads, it shows every player's evidence class (Normal, Elevated, High or not enough data) and how
many analyzed matches it is based on. For Elevated and High players, **F7** shows an extended card
(evidence score, high-risk matches, wall tracking / aim / reaction levels, recent flagged matches), and
a siren plays when a High player is in your match.

Platforms: Windows (installer or Microsoft Store) and Linux (from source, with native Steam and CS2; see
[Running it on Linux](#running-it-on-linux)).

It runs as a plain Electron app, **without Overwolf**. Nothing in it reads or changes CS2's memory,
injects into the game or hooks its drawing:

- **Who is in the match**: Steam's "recently played with" list (Steam > View > Players). CS2 reports
  everyone in your match to Steam when the match loads; a small helper process reads that list from the
  running Steam client. It has no teams, so the list shows one group of players.
- **When the match starts**: CS2's Game State Integration. The app writes
  `gamestate_integration_cheatscanner.cfg` into CS2's `cfg` folder (restart CS2 once after the first
  start) and listens on `127.0.0.1:37215`.
- **Overlay**: a see-through window kept on top of the game; clicks go through to the game. It shows
  itself in warm-up and hides when the match goes live. Needs CS2's display mode **Fullscreen Windowed**
  (Settings > Video); in plain Fullscreen the game is drawn over it.

| Key | What it does |
| --- | --- |
| **Shift+F2** | Show the lobby list (again to hide) |
| **F7** | Show the extended card of flagged players (again to hide) |

Settings also has "Start automatically with Windows, minimized" (installed Windows app only; it starts with
`--minimized`). Both keys can be changed under Settings in the app (letters and digits only with Ctrl or Alt). The server
address is not a setting: the installed app always uses https://cheatscanner.eu (`--server` and
`CHEATSCANNER_SERVER` are for development).

## Running it (Windows PowerShell, one command per line)

The website/API must be running (`cs2-analyzer serve`, see the main README), and Steam must be running
and signed in. Then:

```powershell
cd companion
npm ci
npm start
```

Link the app to your account when it asks, restart CS2 once, and join a match.

To check what the app sees from Steam, run this in a match's warm-up. It prints Steam's players list with
times and the players the app would pick as your match:

```powershell
npm run coplay
```

Without CS2, `npm run start:replay` plays a recorded session (`fixtures/premier-mirage.jsonl`: warm-up,
then live after 30 s). To use one of your own analyzed matches:

```powershell
node tools/replay-from-match.mjs <matchId>
npx electron . --replay=fixtures/local-match.jsonl
```

(Add `--token=<api token>` if your server has sign-in turned on; create one under Settings.)

Other options, passed after `electron .`:

| Option | What it does |
| --- | --- |
| `--replay` / `--replay=<file>` | Play a recording instead of live data |
| `--server=<url>` | Use another server. Default: `http://localhost:8000` when run from source, `https://cheatscanner.eu` when packaged |
| `--dev-ui=<url>` | Load the pages from `npm run dev:ui` (hot reload) |

`npm run dev:ui` also works in a normal browser with a simulated app: open
`http://localhost:5173/index.html?screen=lobby` (or `unlinked`, `linking`, `waiting`, `problem`) and
`overlay.html?screen=lobby` or `overlay.html?screen=detail`.

## Running it on Linux

Linux works from source, with native Steam and native CS2. There is no packaged Linux build yet.

Requirements:

- **Steam**, the native package (not Flatpak or Snap: their sandboxes keep the Steam client out of reach of
  other processes), running and signed in. The app finds it in `~/.steam/debian-installation` or
  `~/.local/share/Steam`.
- **CS2**, native, in one of Steam's libraries (found through `libraryfolders.vdf`), with display mode
  **Fullscreen Windowed**.
- **An X11 session.** The overlay window and its global hotkeys have been tried on X11 (Linux Mint 22.3,
  Cinnamon); Wayland is untested.
- **Node 22.12 or newer** (`engines` in `package.json`; tested with Node 24). Distro packages are often
  older, so check `node --version` and use nvm, NodeSource or similar if needed.

```bash
cd companion
npm ci
npm start -- --server=https://cheatscanner.eu
```

From source the app talks to `http://localhost:8000` unless told otherwise (only the packaged Windows app
defaults to cheatscanner.eu), hence `--server`. Replay mode, the other options and `npm run dev:ui` above work
the same in a Linux shell.

Link the app to your account when it asks, restart CS2 once, and join a match. On first start the app writes
`gamestate_integration_cheatscanner.cfg` into
`<Steam library>/steamapps/common/Counter-Strike Global Offensive/game/csgo/cfg/`.

`npm run coplay` prints Steam's players list; compare it with Steam > View > Players. If it says Steam isn't
running, check that native Steam is signed in and that `~/.steam/steam.pid` names a live process.

If Electron refuses to start with a `chrome-sandbox` (SUID sandbox) error, which distros that restrict
unprivileged user namespaces do (Ubuntu 24.04 and its derivatives), give the bundled helper the permissions it
needs:

```bash
sudo chown root:root node_modules/electron/dist/chrome-sandbox
sudo chmod 4755 node_modules/electron/dist/chrome-sandbox
```

Settings and the linked account are kept in `~/.config/Cheatscanner`. The API token is encrypted with the
desktop keyring through Electron's `safeStorage`; with no keyring available it is stored as plain text in
`settings.json`. "Start automatically with Windows" and the installer and Store packages are Windows only.

## Building the installer (Windows PowerShell, one command per line)

```powershell
cd companion
npm ci
npm run dist
```

This writes `release\Cheatscanner-Setup-<version>.exe` (the version comes from `package.json`). The installed
app talks to `https://cheatscanner.eu`; set `CHEATSCANNER_SERVER` before starting it to use another server.
It installs per user (no admin prompt), adds Start menu and desktop shortcuts, and keeps its settings in
`%APPDATA%\Cheatscanner`, the same place `npm start` uses, so a linked account carries over.

The installer isn't code-signed yet, so Windows SmartScreen shows "Windows protected your PC" the first
time: click **More info**, then **Run anyway**. Signing needs a code-signing certificate; electron-builder
picks one up from `CSC_LINK` / `CSC_KEY_PASSWORD`.

Build on Windows. On Linux or macOS, electron-builder needs Wine for the Windows installer.

## Microsoft Store package

```powershell
cd companion
npm ci
npm run dist:store
```

This writes `release\Cheatscanner-<version>.appx`, an MSIX-family package with the Store identity from
Partner Center (`build.appx` in `package.json`). Upload it in Partner Center under the app's submission,
Packages. Microsoft signs it after certification, so no certificate is needed, and the Store handles updates
(raise `version` in `package.json` for each new upload). It must be built on Windows 10 or 11. The tile images
are in `assets/appx`.

### Releases from GitHub

Actions > **companion-release** > Run workflow, with a version such as `0.2.0` (or push a tag
`companion-v0.2.0`). It builds the installer and the Store package on Windows with that version, attaches both
to a GitHub release, and submits the package to the Store when the `PARTNER_CENTER_*` secrets exist (see the
top of `.github/workflows/companion-release.yml`). The first Store submission is made by hand in Partner Center;
later ones reuse its listing.

Differences from the `.exe` install: "Start automatically with Windows" isn't offered (Store apps can't add
a plain login item), and Windows may keep the app's settings in a separate place, so it asks to be linked
again once.

## Overwolf (optional)

The Overwolf route still works if Overwolf approves the app: `npm run start:overwolf` (ow-electron with
`--overwolf`) takes the roster from Overwolf's CS2 game events and draws the overlay with Overwolf's
overlay package. `--record` then saves Overwolf's CS2 data to `%APPDATA%\Cheatscanner\recordings`.

## How it fits together

```text
Steam players list ─┐ (helper process)          ┌─ desktop window (src/renderer/desktop.tsx)
CS2 game state ─────┼─► GameSource ─► Controller ─┤
 (or Overwolf,      │   game/*.ts        │        └─ overlay window (src/renderer/overlay.tsx)
  or a replay)      │                    ▼
                    │            Backend (backend.ts) ──HTTPS──► /companion/pair, /lobby/risk, /me, /site-info
```

- `src/main/steam/`: `coplay.ts` (reads Steam's players list; runs in `coplay-worker.ts`, a separate
  process), `pick.ts` (picks the current match from the list), `gsi.ts` (CS2 game state: cfg file and
  listener), `coplay-cli.ts` (`npm run coplay`).
- `src/main/game/`: `steam.ts` (default source), `overwolf.ts`, `replay.ts`, `gep.ts`, `recorder.ts`.
- `src/main/controller.ts`: linking, lobby lookups, overlay visibility (warm-up / live / hotkeys), siren;
  no Electron code, so it is unit-tested.
- `src/main/main.ts`: windows, hotkeys, IPC, the helper process.
- `src/main/preload.ts`: the only bridge the pages get. The token stays in the main process, encrypted
  with the OS key store (`safeStorage`: Windows DPAPI, the desktop keyring on Linux) in `settings.json`.

Linking works like signing in a TV app: the app asks the server for a code, opens
`cheatscanner.eu/#/link?code=...` in the browser, the user signs in with Steam and confirms the code, and
the app receives its own API token. It shows up (and can be revoked) on the website under Settings.

## Checks

```powershell
npm test
npm run build
```

To offer it on the website's front page, send it to the server with `tools\deploy\push-companion.ps1`
(see `docs/deploy/hetzner.md`, section 10).
The app is also in the Microsoft Store (https://apps.microsoft.com/detail/9N3R274VP3GV); the front page links
there first and offers the installer from the server as a fallback.
