import type { LobbyRow } from "../shared/types";
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

