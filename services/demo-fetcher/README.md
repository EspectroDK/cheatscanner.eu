# Demo fetcher

Turns queued CS2 match share codes into replay download URLs for the Cheatscanner API.

Valve only hands out a match's replay URL through the CS2 Game Coordinator, and the Game
Coordinator only talks to a logged-in Steam account that owns CS2. So this service logs in a
**dedicated bot account**, never a user's account. It tells Steam the account is "playing" CS2 but
never starts the game, so there is no game process and nothing for VAC to inspect.

```
API queue ──claim──▶ demo fetcher ──requestGame──▶ Game Coordinator
    ▲                     │
    └──── demo URL ───────┘   (the API then downloads, analyzes and deletes the demo)
```

## Setup

1. Create a separate Steam account and give it CS2 (free). Valve may limit accounts that have
   never bought anything; if the Game Coordinator never answers, that is the first thing to check.
2. Pick a long random `CS2A_SERVICE_TOKEN` and give the same value to the API and the fetcher.
3. Run it:

```bash
cd services/demo-fetcher
npm install
CS2A_API_URL=http://localhost:8000 CS2A_SERVICE_TOKEN=... \
STEAM_BOT_USERNAME=... STEAM_BOT_PASSWORD=... node index.js
```

Settings are also read from a `.env` file in this folder or the repository root (shell variables
win). On the first run Steam Guard asks for a code on the console (or set `STEAM_BOT_SHARED_SECRET` for the
account's mobile authenticator). After that, a refresh token is kept in `FETCHER_STATE_DIR`
(default `./state`) and the password is no longer needed.

| Variable | Default | Meaning |
|---|---|---|
| `CS2A_API_URL` | `http://localhost:8000` | Cheatscanner API |
| `CS2A_SERVICE_TOKEN` | required | Same value as the API's |
| `STEAM_BOT_USERNAME` | required | Bot account name |
| `STEAM_BOT_PASSWORD` | first run only | Bot account password |
| `STEAM_BOT_SHARED_SECRET` | none | Mobile authenticator secret, for unattended logins |
| `FETCHER_STATE_DIR` | `./state` | Where the login token is kept |
| `FETCHER_IDLE_SECONDS` | `30` | Wait when the queue is empty |
| `FETCHER_GAP_SECONDS` | `5` | Pause between Game Coordinator requests |
| `FETCHER_CHAT_SECONDS` | `15` | How often to check for Steam chat messages to send |

A match whose demo Valve has already deleted (after a few weeks) is reported as expired; a timeout
goes back to the queue and is retried up to three times.

## Steam chat messages

Users can switch on a Steam chat message for when a match of theirs has been analyzed (website
Settings). The bot sends them from the same login:

- It accepts a friend request only from a user who switched the messages on (it asks
  `GET /internal/chat/allowed/{steamId}`); other requests stay pending and are checked again every few
  minutes, so switching it on after sending the request also works.
- Every `FETCHER_CHAT_SECONDS` it claims waiting messages (`POST /internal/chat/claim`, which also tells
  the API the bot's Steam ID and friend list for the Settings page), sends them and reports each one.
  A message for someone who isn't a friend is skipped, not retried.
- A Steam account that has never bought anything is "limited": it can accept friend requests and chat,
  but can't send friend requests itself, which is why users add the bot and not the other way round.
  Steam also caps the friend list (250 at level 0, +5 per Steam level).
