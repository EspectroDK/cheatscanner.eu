// Makes a replay recording from a match your server has analyzed, so the app and overlay show real
// players and classes without CS2 or Overwolf:
//
//   node tools/replay-from-match.mjs <matchId> [--server=http://localhost:8000] [--token=<api token>] [--me=<steamId>]
//   npm run build
//   npx ow-electron . --replay=fixtures/local-match.jsonl
//
// The output file holds real names and Steam IDs; it is git-ignored (fixtures/local-*.jsonl).

import { writeFileSync } from "node:fs";

const args = Object.fromEntries(process.argv.slice(2).filter((a) => a.startsWith("--")).map((a) => {
  const [k, ...v] = a.slice(2).split("=");
  return [k, v.join("=") || true];
}));
const matchId = process.argv.slice(2).find((a) => !a.startsWith("--"));
if (!matchId) {
  console.error("usage: node tools/replay-from-match.mjs <matchId> [--server=URL] [--token=TOKEN] [--me=STEAMID]");
  process.exit(2);
}
const server = String(args.server ?? "http://localhost:8000").replace(/\/+$/, "");
const headers = args.token ? { Authorization: `Bearer ${args.token}` } : {};
const res = await fetch(`${server}/matches/${encodeURIComponent(matchId)}`, { headers });
if (!res.ok) {
  console.error(`the server answered ${res.status}: ${await res.text()}`);
  process.exit(1);
}
const match = await res.json();
const side = (team) => (team === 2 ? "T" : team === 3 ? "CT" : "");
const me = String(args.me ?? match.players[0]?.steamId ?? "");
const lines = [
  { t: 0, kind: "running", value: true },
  { t: 800, kind: "info", category: "live_data", key: "provider", value: JSON.stringify({ steam_id: me }) },
  { t: 1200, kind: "info", category: "match_info", key: "map", value: match.map ?? "" },
];
match.players.forEach((p, i) => lines.push({
  t: 1500 + i * 300, kind: "info", category: "match_info", key: `roster_${i}`,
  value: JSON.stringify({ nickname: p.name ?? p.steamId, steamid: p.steamId, team: side(p.team), is_local: p.steamId === me ? "1" : "0" }),
}));
lines.push({ t: 120000, kind: "running", value: false });
const out = String(args.out ?? "fixtures/local-match.jsonl");
writeFileSync(out, lines.map((l) => JSON.stringify(l)).join("\n") + "\n");
console.log(`wrote ${out} with ${match.players.length} players from ${match.map ?? "unknown map"}`);
