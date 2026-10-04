// `npm run coplay`: prints Steam's players list and which players the app would pick as your match.
// Use it in a match's warm-up to check what the app sees. Needs Windows and a running, signed-in Steam.
// Optional: your SteamID64 as an argument (`npm run coplay -- 7656...`) to also print your own rich presence.

import { CS2_APP_ID, readCoplay } from "./coplay";
import { pickFriends, pickMatchPlayers } from "./pick";

const now = Math.floor(Date.now() / 1000);
try {
  const local = process.argv[2] ?? null;
  const { localName, entries, friends = [], localPresence } = readCoplay(local);
  console.log(`Signed in to Steam as: ${localName ?? "?"}`);
  console.log(`Players list: ${entries.length} entries (newest first)\n`);
  for (const e of [...entries].sort((a, b) => b.time - a.time).slice(0, 40)) {
    const ago = now - e.time;
    const when = ago < 90 ? `${ago} s ago` : ago < 5400 ? `${Math.round(ago / 60)} min ago` : `${Math.round(ago / 3600)} h ago`;
    console.log(`${String(e.time).padEnd(11)} ${when.padEnd(11)} ${e.appId === CS2_APP_ID ? "CS2 " : String(e.appId).padEnd(4)} ${e.steamId}  ${e.name ?? "(name unknown)"}`);
  }
  const pick = pickMatchPlayers(entries, { now });
  console.log(`\nThe app would show these ${pick.players.length} players as your match:`);
  for (const p of pick.players) console.log(`  ${p.steamId}  ${p.name ?? "(name unknown)"}`);
  if (pick.others.length) console.log(`\nLeft out (other recent CS2 entries): ${pick.others.map((p) => p.name ?? p.steamId).join(", ")}`);
  const show = (p: Record<string, string>) => Object.entries(p).map(([k, v]) => `${k}=${v}`).join("  ") || "(no rich presence)";
  if (localPresence) console.log(`\nYour rich presence: ${show(localPresence)}`);
  console.log(`\nFriends playing CS2 now: ${friends.length}`);
  for (const f of friends) console.log(`  ${f.steamId}  ${f.name ?? "(name unknown)"}\n      ${show(f.presence)}`);
  const map = localPresence?.["game:map"] ?? null;
  const extra = pickFriends(friends, { map, localPresence, exclude: new Set(pick.players.map((p) => p.steamId)), max: 9 - pick.players.length });
  if (extra.length) console.log(`\nFriends the app would add to your match: ${extra.map((f) => f.name ?? f.steamId).join(", ")}`);
} catch (e) {
  console.error(e instanceof Error ? e.message : e);
  process.exitCode = 1;
}
