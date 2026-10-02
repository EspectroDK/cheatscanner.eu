// `npm run coplay`: prints Steam's players list and which players the app would pick as your match.
// Use it in a match's warm-up to check what the app sees. Needs Windows and a running, signed-in Steam.

import { CS2_APP_ID, readCoplay } from "./coplay";
import { pickMatchPlayers } from "./pick";

const now = Math.floor(Date.now() / 1000);
try {
  const { localName, entries } = readCoplay();
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
} catch (e) {
  console.error(e instanceof Error ? e.message : e);
  process.exitCode = 1;
}
