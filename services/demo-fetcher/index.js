// Cheatscanner demo fetcher.
//
// Turns CS2 match share codes into replay download URLs. Valve only answers this question
// through the CS2 Game Coordinator, which only talks to a logged-in Steam account that owns
// CS2, so this service logs in a dedicated bot account (never a user's account).
//
// It never starts the game: steam-user only tells Steam the account is "in" app 730, so
// there is no game process and nothing for VAC to look at.
//
// Loop: claim a queued share code from the API -> ask the GC -> report the demo URL (or why
// there is none) -> the API downloads and analyzes the demo.
//
// Second loop: Steam chat messages. Users who switched them on (website Settings) add the bot as a
// friend; the bot accepts only those, and sends each one a message with the link when a match of
// theirs has been analyzed.

import fs from "node:fs";
import path from "node:path";
import GlobalOffensive from "globaloffensive";
import SteamTotp from "steam-totp";
import SteamUser from "steam-user";

// Settings from .env (the fetcher's own folder, then the repository root), like Docker Compose would
// pass them. Variables already set in the shell win.
for (const file of [path.resolve(".env"), new URL("../../.env", import.meta.url)]) {
  if (!fs.existsSync(file)) continue;
  for (const line of fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const m = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (!m || !m[2] || process.env[m[1]] !== undefined) continue;
    process.env[m[1]] = m[2].replace(/^(["'])(.*)\1$/, "$2");
  }
}

const env = (name, fallback) => {
  const v = process.env[name] ?? fallback;
  if (v === undefined) {
    console.error(`missing environment variable ${name} (see README.md)`);
    process.exit(2);
  }
  return v;
};

const API = env("CS2A_API_URL", "http://localhost:8000").replace(/\/$/, "");
const SERVICE_TOKEN = env("CS2A_SERVICE_TOKEN");
const USERNAME = env("STEAM_BOT_USERNAME");
const PASSWORD = process.env.STEAM_BOT_PASSWORD;
const SHARED_SECRET = process.env.STEAM_BOT_SHARED_SECRET; // optional: mobile authenticator secret
const STATE_DIR = env("FETCHER_STATE_DIR", "./state");
const IDLE_MS = Number(env("FETCHER_IDLE_SECONDS", "30")) * 1000;
const GAP_MS = Number(env("FETCHER_GAP_SECONDS", "5")) * 1000; // be gentle with the GC
const CHAT_MS = Number(env("FETCHER_CHAT_SECONDS", "15")) * 1000;
const GC_TIMEOUT_MS = 20000;
const Rel = SteamUser.EFriendRelationship;

const tokenFile = path.join(STATE_DIR, "refresh-token");
fs.mkdirSync(STATE_DIR, { recursive: true });

const user = new SteamUser({ renewRefreshTokens: true });
const csgo = new GlobalOffensive(user);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function logOn() {
  if (fs.existsSync(tokenFile)) {
    user.logOn({ refreshToken: fs.readFileSync(tokenFile, "utf8").trim() });
    return;
  }
  if (!PASSWORD) {
    console.error("no saved login: set STEAM_BOT_PASSWORD for the first run");
    process.exit(2);
  }
  // Without a shared secret, steam-user asks for the Steam Guard code on the console once.
  const details = { accountName: USERNAME, password: PASSWORD };
  if (SHARED_SECRET) details.twoFactorCode = SteamTotp.generateAuthCode(SHARED_SECRET);
  user.logOn(details);
}

user.on("refreshToken", (token) => fs.writeFileSync(tokenFile, token, { mode: 0o600 }));
user.on("loggedOn", () => {
  console.log("logged in to Steam; connecting to the CS2 Game Coordinator");
  user.gamesPlayed([730]);
});
user.on("error", (err) => {
  console.error(`Steam login error: ${err.message}`);
  if (err.eresult === SteamUser.EResult.InvalidPassword || err.eresult === SteamUser.EResult.AccessDenied) {
    fs.rmSync(tokenFile, { force: true });
  }
  setTimeout(logOn, 60000);
});

async function api(method, route, body) {
  const res = await fetch(`${API}${route}`, {
    method,
    headers: { Authorization: `Bearer ${SERVICE_TOKEN}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  if (!res.ok) throw new Error(`API ${method} ${route}: HTTP ${res.status}`);
  return res.json();
}

// The replay URL is in the last round's stats ("map" field), e.g.
// http://replay183.valve.net/730/003xxxxxxxxxxxxxxxxx_yyyyyyyyyy.dem.bz2
export function demoUrlFrom(match) {
  const rounds = [...(match?.roundstatsall ?? []), match?.roundstats_legacy].filter(Boolean);
  for (let i = rounds.length - 1; i >= 0; i--) {
    const url = rounds[i].map;
    if (typeof url === "string" && url.endsWith(".dem.bz2")) return url;
  }
  return null;
}

function requestGame(job) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      csgo.removeListener("matchList", onList);
      reject(new Error("Game Coordinator did not answer in time"));
    }, GC_TIMEOUT_MS);
    function onList(matches) {
      const match = (matches ?? []).find((m) => String(m.matchid) === job.matchId) ?? (matches ?? [])[0];
      if (matches?.length && match && String(match.matchid) !== job.matchId) return; // another request's answer
      clearTimeout(timer);
      csgo.removeListener("matchList", onList);
      resolve(match ?? null);
    }
    csgo.on("matchList", onList);
    csgo.requestGame({ matchId: job.matchId, outcomeId: job.reservationId, token: job.tvPort });
  });
}

async function work() {
  let waitingSince = Date.now();
  let saidIdle = false;
  for (;;) {
    if (!csgo.haveGCSession) {
      const secs = Math.round((Date.now() - waitingSince) / 1000);
      if (secs >= 30 && secs % 30 < 5) {
        console.log(`still waiting for the CS2 Game Coordinator (${secs}s); does the bot account have CS2 in its library?`);
      }
      await sleep(5000);
      continue;
    }
    waitingSince = Date.now();
    let job;
    try {
      job = await api("POST", "/internal/sharecodes/claim");
    } catch (err) {
      console.error(err.message);
      await sleep(IDLE_MS);
      continue;
    }
    if (!job) {
      if (!saidIdle) console.log("queue is empty; checking again every " + IDLE_MS / 1000 + "s");
      saidIdle = true;
      await sleep(IDLE_MS);
      continue;
    }
    saidIdle = false;
    console.log(`${job.shareCode}: asking the Game Coordinator for the demo`);
    let result;
    try {
      const match = await requestGame(job);
      const url = demoUrlFrom(match);
      // No match or no URL: Valve no longer has the demo (they expire after some weeks).
      result = url ? { demoUrl: url } : { expired: true, error: "Valve no longer has this demo" };
      const played = Number(match?.matchtime);
      if (url && played > 0) result.matchTime = played; // lets the site date the match and Valve's download link
    } catch (err) {
      result = { error: err.message };
    }
    try {
      await api("POST", `/internal/sharecodes/${encodeURIComponent(job.shareCode)}/result`, result);
      console.log(`${job.shareCode}: ${result.demoUrl ? "demo found" : result.error}`);
    } catch (err) {
      console.error(err.message);
    }
    await sleep(GAP_MS);
  }
}

// ------------------------------------------------------------------ Steam chat messages

const requestChecked = new Map(); // SteamID64 -> when we last asked the API about their friend request
const greet = new Set();          // friend requests we just accepted: say hello once they are friends

async function acceptIfAllowed(id) {
  const last = requestChecked.get(id);
  if (last && Date.now() - last < 5 * 60000) return; // not opted in (yet): ask again in a few minutes
  requestChecked.set(id, Date.now());
  try {
    const { allowed } = await api("GET", `/internal/chat/allowed/${id}`);
    if (!allowed) return;
    greet.add(id);
    await user.addFriend(id); // accepts their pending request
    console.log(`accepted the friend request of ${id}`);
  } catch (err) {
    console.error(`friend request of ${id}: ${err.message}`);
  }
}

export function pendingRequests(friends) {
  return Object.entries(friends ?? {}).filter(([, rel]) => rel === Rel.RequestRecipient).map(([id]) => id);
}

export function friendIds(friends) {
  return Object.entries(friends ?? {}).filter(([, rel]) => rel === Rel.Friend).map(([id]) => id);
}

user.on("friendRelationship", async (steamID, rel) => {
  const id = steamID.getSteamID64();
  if (rel === Rel.RequestRecipient) {
    requestChecked.delete(id); // a new request: check right away
    await acceptIfAllowed(id);
  } else if (rel === Rel.Friend && greet.delete(id)) {
    user.chat.sendFriendMessage(id, "Hi! I'll message you here when a match of yours has been analyzed. " +
      "You can switch this off under Settings on the website.").catch((err) => console.error(`greeting ${id}: ${err.message}`));
  }
});

// Result for the API: sent, not a friend (nothing to retry), or an error (retried a few times).
export async function deliver(message, friends, send) {
  if (friends?.[message.steamId] !== Rel.Friend) return { notFriend: true };
  try {
    await send(message.steamId, message.text);
    return { sent: true };
  } catch (err) {
    return { error: err.message };
  }
}

async function chatLoop() {
  for (;;) {
    await sleep(CHAT_MS);
    if (!user.steamID) continue; // not logged in
    for (const id of pendingRequests(user.myFriends)) await acceptIfAllowed(id);
    let messages;
    try {
      messages = await api("POST", "/internal/chat/claim",
        { botSteamId: user.steamID.getSteamID64(), friends: friendIds(user.myFriends) });
    } catch (err) {
      console.error(err.message);
      continue;
    }
    for (const m of messages ?? []) {
      const result = await deliver(m, user.myFriends, (id, text) => user.chat.sendFriendMessage(id, text));
      try {
        await api("POST", `/internal/chat/${m.id}/result`, result);
        console.log(`chat message ${m.id}: ${result.sent ? "sent" : result.notFriend ? "not a friend" : result.error}`);
      } catch (err) {
        console.error(err.message);
      }
      await sleep(1000);
    }
  }
}

if (process.argv[1] && import.meta.url.endsWith(path.basename(process.argv[1]))) {
  csgo.on("connectedToGC", () => console.log("connected to the CS2 Game Coordinator"));
  csgo.on("disconnectedFromGC", (reason) => console.log(`disconnected from the Game Coordinator (${reason})`));
  logOn();
  work();
  chatLoop();
}
