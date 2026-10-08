import type { LobbyRow, SteamBans } from "../shared/types";
import { ClassBadge } from "./brand";

/** A player's class and analyzed-match count, or why there is none. */
export function PlayerClass({ r, compact }: { r: LobbyRow; compact?: boolean }) {
  if (r.status === "no-steam-id") return <span className="muted small" title="The game didn't report this player's Steam ID">No Steam ID</span>;
  if (r.status === "loading") return <span className="skeleton" aria-label="Looking up" />;
  if (r.status === "error" || !r.classification) return <span className="muted small">–</span>;
  return (
    <span className="class">
      <ClassBadge value={r.classification} compact={compact} />
      {r.matchesAnalyzed > 0 && (
        <span className="matches" title={`Based on ${r.matchesAnalyzed} analyzed matches`}>{r.matchesAnalyzed}</span>
      )}
    </span>
  );
}

const RANK = { HIGH: 0, ELEVATED: 1 } as Record<string, number>;

/** Players with an extended card, HIGH before ELEVATED, strongest evidence first (the F7 view). */
export function flagged(rows: LobbyRow[]): LobbyRow[] {
  return rows
    .filter((r) => r.detail && !r.isLocal && (r.classification === "HIGH" || r.classification === "ELEVATED"))
    .sort((a, b) => (RANK[a.classification!] - RANK[b.classification!]) || b.detail!.evidenceScore - a.detail!.evidenceScore);
}

/** "3 days ago" for a match date. */
export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "date unknown";
  const days = Math.floor((now - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 60) return `${days} days ago`;
  return `${Math.round(days / 30)} months ago`;
}


/** "2 VAC bans", "VAC + game ban". */
export function banLabel(b: SteamBans): string {
  if (b.vacBans && b.gameBans) return "VAC + game ban";
  if (b.vacBans) return b.vacBans > 1 ? `${b.vacBans} VAC bans` : "VAC ban";
  return b.gameBans > 1 ? `${b.gameBans} game bans` : "Game ban";
}

/** A Steam ban on record: account-level, a separate fact next to the evidence class, never an alert. */
export function BanTag({ bans }: { bans: SteamBans | null | undefined }) {
  if (!bans) return null;
  return (
    <span className="ban" title={`Steam: ${banLabel(bans)} on record, last one ${bans.daysSinceLastBan} days ago. ` +
      "Account-level and can come from any game; not part of the evidence class."}>
      {banLabel(bans)}
    </span>
  );
}

/** Players with a Steam ban on record that have no extended card of their own (shown under F7). */
export function bannedOnly(rows: LobbyRow[]): LobbyRow[] {
  const cards = new Set(flagged(rows).map((r) => r.steamId));
  return rows.filter((r) => r.bans && !r.isLocal && !cards.has(r.steamId));
}
