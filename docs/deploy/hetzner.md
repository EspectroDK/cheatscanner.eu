# Putting Cheatscanner online on Hetzner

The one-time setup of a public server, step by step: one Hetzner Cloud server (about €10–15 a month
with backups) running the Docker Compose stack behind Caddy. The examples use the domain
`cheatscanner.eu`; replace it with your own. Commands on your PC are for Windows PowerShell, one per
line. `<IP>` means the server's IPv4 address from the Hetzner console.

Once this is done, every merge into `main` deploys itself (the **deploy** workflow in GitHub Actions),
and new map meshes go up with one command (section 9).

## 1. SSH key on your PC (skip if you already have `~\.ssh\id_ed25519.pub`)

```powershell
ssh-keygen -t ed25519
Get-Content $HOME\.ssh\id_ed25519.pub
```

Press Enter at the questions (a passphrase is optional). Copy the line that `Get-Content` prints.

## 2. Hetzner account and server

1. Sign up at <https://accounts.hetzner.com/signUp> and add a payment method (Hetzner may ask for ID).
2. In the Cloud console (<https://console.hetzner.cloud>) create a project called **Cheatscanner**.
3. **Add Server**:
   - Location: **Nuremberg** or **Falkenstein** (Germany).
   - Image: **Ubuntu 24.04**.
   - Type: **Shared vCPU, x86 (Intel/AMD), CX33** (4 vCPU, 8 GB RAM, 80 GB).
   - Networking: keep **Public IPv4** and **Public IPv6** on.
   - SSH keys: **Add SSH key**, paste the line from step 1.
   - Backups: **on** (+20%).
   - Cloud config: paste the whole of `deploy/cloud-init.yaml` from the repository.
   - Name: `cheatscanner`. Then **Create & Buy now**.
4. Note the IPv4 and IPv6 addresses shown for the server.
5. Hetzner's data processing agreement (GDPR, "order processing" / AVV): conclude it online in the account
   area at <https://accounts.hetzner.com>. It covers the personal data the site stores.

Give the server about 5 minutes to finish setting itself up, then check:

```powershell
ssh deploy@<IP> docker ps
```

Answer `yes` to the fingerprint question. An empty table with the headers `CONTAINER ID  IMAGE ...` means
it's ready.

## 3. DNS for your domain

At the registrar where you bought the domain, set these records (TTL 300 is fine to start):

| Type | Name | Value |
|---|---|---|
| A | `@` (cheatscanner.eu) | the server's IPv4 |
| A | `www` | the server's IPv4 |
| AAAA | `@` | the server's IPv6 (the address ending in `::1`) |
| AAAA | `www` | the same IPv6 |

Remove any parking-page records for `@` and `www` the registrar set up. HTTPS certificates are fetched
automatically once these point at the server.

## 4. Let GitHub deploy to the server

```powershell
ssh deploy@<IP> cheatscanner-github-key
```

It prints three values. In GitHub: repository **Settings > Secrets and variables > Actions > New
repository secret**, add each one:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | the line under `DEPLOY_HOST` (the IP) |
| `DEPLOY_KNOWN_HOSTS` | the line under `DEPLOY_KNOWN_HOSTS` |
| `DEPLOY_SSH_KEY` | everything from `-----BEGIN OPENSSH PRIVATE KEY-----` to `-----END OPENSSH PRIVATE KEY-----`, both lines included |

## 5. First deploy and the settings file

1. GitHub: **Actions > deploy > Run workflow** (branch `main`).
2. The first run stops with *"Created /srv/cheatscanner/.env with new random secrets"*. That's expected.
3. Fill in the rest:

   ```powershell
   ssh deploy@<IP>
   nano /srv/cheatscanner/.env
   ```

   Fill in `CS2A_ADMIN_STEAM_IDS` (your SteamID64), `CS2A_STEAM_API_KEY`, `CS2A_CONTACT_EMAIL`, and the bot's
   `STEAM_BOT_USERNAME`, `STEAM_BOT_PASSWORD` and `STEAM_BOT_SHARED_SECRET`. Leave the generated values alone,
   except `CS2A_SECRET_KEY` if you move your local database over (section 11).
   Save with Ctrl+O, Enter, then leave with Ctrl+X, and type `exit`.
4. **Copy `BORG_PASSPHRASE` from that file into your password manager.** Without it the backups can't be
   opened if the server is lost.
5. Run the workflow again. When it's green, open <https://cheatscanner.eu> and sign in with Steam.

Steam Web API key: <https://steamcommunity.com/dev/apikey> asks for a domain; use yours.

## 6. The Steam bot

Check that it logged in:

```powershell
ssh deploy@<IP>
cd /srv/cheatscanner
docker compose logs --tail 30 demo-fetcher
```

Steam often asks for a code the first time an account logs in from a new place. With
`STEAM_BOT_SHARED_SECRET` set the bot answers it itself. Without it, enter the code once by hand:

```bash
docker compose stop demo-fetcher
docker compose run --rm demo-fetcher
```

Type the code from the bot account's e-mail or app, wait for the login message, press Ctrl+C, then
`docker compose up -d demo-fetcher`. The login is remembered after that.

## 7. Backups (Storage Box)

1. Hetzner console: **Storage Boxes > Create**, **BX11**, same location as the server. In its settings,
   turn on **SSH support** and set a password. Note the user name (`u123456`).
2. Give the server access (asks for the Storage Box password once):

   ```powershell
   ssh deploy@<IP>
   cat ~/.ssh/id_ed25519.pub | ssh -p23 u123456@u123456.your-storagebox.de install-ssh-key
   nano /srv/cheatscanner/.env
   ```

   Set `BORG_REPO=ssh://u123456@u123456.your-storagebox.de:23/./cheatscanner` (your user name twice).
3. Run a first backup and check it:

   ```bash
   /srv/cheatscanner/deploy/backup.sh
   ```

   The last line says `backup ok`. After that it runs every night at 03:17 (server time, UTC) and logs to
   `/srv/cheatscanner/backups/backup.log`.

To restore the database from the latest archive (also a good test once):

```bash
cd /srv/cheatscanner
export BORG_REPO=$(grep ^BORG_REPO= .env | cut -d= -f2-) BORG_PASSPHRASE=$(grep ^BORG_PASSPHRASE= .env | cut -d= -f2-)
borg list
borg extract ::<archive name from the list> backups/
docker compose cp backups/db-<date>.dump db:/tmp/restore.dump
docker compose exec db pg_restore -U cs2 -d cs2 --clean --if-exists /tmp/restore.dump
```

## 8. Being told when something breaks

- **Site down**: a free monitor at <https://uptimerobot.com> (or Better Stack) on
  `https://cheatscanner.eu/health`, every 5 minutes, alerts to your e-mail.
- **Backup failed or disk filling up**: a free check at <https://healthchecks.io> with period **1 day** and
  grace **2 hours**. Put its ping URL in `.env` as `HEALTHCHECK_URL`. The backup pings it every night, and
  reports a failure when the backup fails or less than 30 GB is free.

The Admin page (`https://cheatscanner.eu/#/admin`) shows the queue, disk space and, under **Map meshes**,
which maps need a new mesh.

## 9. Updating map meshes

After a CS2 update that changes maps (the Admin page marks them **out of date**), update the game, then on
your PC:

```powershell
cd "C:\path\to\cheatscanner"
.awpy\Scripts\python tools\geometry\build_tris.py --s2v C:\path\to\Source2Viewer-CLI.exe
.awpy\Scripts\python tools\geometry\build_tris.py --s2v C:\path\to\Source2Viewer-CLI.exe --out data\maps\render
powershell -ExecutionPolicy Bypass -File tools\deploy\push-maps.ps1
```

The second `build_tris` line builds the meshes the evidence clips are drawn with (see
[docs/geometry.md](../geometry.md)). To push only some maps, add their names: `... push-maps.ps1 de_mirage de_train`. The server checks each
file and only installs good ones; the next analysis on those maps uses them, with no restart.
Until DNS works you can push to the IP: `... push-maps.ps1 -Server deploy@<IP>`.

## 10. Publishing the companion app

The front page offers the newest `Cheatscanner-Setup-<version>.exe` in `/srv/cheatscanner/data/downloads` on
the server (served at `/download/companion`; without a file there the page says the app is coming soon). The
installer is not in git. Build it and send it from your PC, in the repository folder:

```powershell
cd companion
npm ci
npm run dist
cd ..
powershell -ExecutionPolicy Bypass -File tools\deploy\push-companion.ps1
```

The script sends the newest installer in `companion\release`. To send another file:
`... push-companion.ps1 -Installer C:\path\Cheatscanner-Setup-0.1.0.exe`. For a new version, raise `version` in
`companion/package.json` first; older installers can stay on the server, the newest one is offered.

## 11. Optional: bring your local database along

Only if you want the matches analyzed on your PC on the server too. On your PC, in the repository folder
with the local stack running:

```powershell
docker compose exec -T db pg_dump -U cs2 -Fc -f /tmp/cs2.dump cs2
docker compose cp db:/tmp/cs2.dump .\cs2.dump
scp .\cs2.dump deploy@<IP>:/srv/cheatscanner/backups/
```

Then on the server:

```bash
cd /srv/cheatscanner
docker compose cp backups/cs2.dump db:/tmp/cs2.dump
docker compose exec db pg_restore -U cs2 -d cs2 --clean --if-exists --no-owner /tmp/cs2.dump
```

Also put your PC's `CS2A_SECRET_KEY` into the server's `.env` (users' stored Steam codes only decrypt with
it), then `docker compose up -d`. Player profiles also read `data\observations`; copy it with
`scp -r data\observations deploy@<IP>:/srv/cheatscanner/data/`.

## 12. Analysis workers

Demos are analyzed by separate `worker` containers that share one queue in the database; the website
(`api`) only takes uploads and puts them in the queue, so analyses never slow the site down and a deploy
doesn't lose queued demos. The server runs `CS2A_WORKERS` of them (default 2).

- **When to add one**: the Admin page shows *Waiting for analysis* and how long the oldest demo has waited.
  If that is regularly more than a few minutes, add a worker.
- **How**: set `CS2A_WORKERS=3` in `/srv/cheatscanner/.env`, then `docker compose up -d` (or wait for the
  next deploy). The Admin page's *Being analyzed* box shows how many workers are running.
- **Limits of a CX33** (4 vCPU, 8 GB, shared with the database and the website): each analysis uses about
  one CPU core and, for a long demo, a few GB of RAM, so memory runs out first. Check before adding one:

  ```bash
  docker stats --no-stream                   # memory per container while demos are being analyzed
  docker compose logs worker | grep "done in"   # seconds per analysis
  ```

  If 3 workers aren't enough, resize the server (Hetzner Console > server > **Rescale**, e.g. CX43 with
  8 vCPU / 16 GB; a reboot, not a move) and raise `CS2A_WORKERS`. A second server only makes sense beyond
  that, and needs uploaded demos to reach it (fetched matches already download from Valve).
- A worker that stops (deploy, crash, out of memory) gives its demo back to the queue; another worker
  continues it. A demo whose worker dies 3 times is marked failed.

## Everyday commands on the server

```bash
ssh deploy@<IP>
cd /srv/cheatscanner
docker compose ps                    # what is running
docker compose logs --tail 50 api    # recent log of one service
docker compose logs -f -t worker     # follow the analyses live (Ctrl+C stops following)
docker compose exec api cs2-analyzer queue   # demos waiting / being analyzed, and running workers
docker compose restart api           # restart one service
```

Rolling back: **Actions > deploy > Run workflow** with an older commit in the *ref* field.
