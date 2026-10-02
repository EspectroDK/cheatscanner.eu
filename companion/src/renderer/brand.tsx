// Cheatscanner brand pieces, matching the website (web/src/art.tsx, web/src/ui.tsx).

import type { EvidenceClass, Side } from "../shared/types";

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="logo">
      <circle cx="14" cy="14" r="9.5" fill="none" stroke="currentColor" strokeWidth="2.6" />
      <path d="M21 21l7 7" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M14 7.5v4M14 16.5v4M7.5 14h4M16.5 14h4" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="14" cy="14" r="1.3" fill="var(--accent)" />
    </svg>
  );
}

/** "cheatscanner.eu" with the TLD in the accent colour. */
export function Wordmark({ domain }: { domain?: string | null }) {
  const [name, tld] = splitDomain(domain);
  return (
    <span className="wordmark">
      {name}
      {tld && <span className="wordmark-tld">.{tld}</span>}
    </span>
  );
}

function splitDomain(domain?: string | null): [string, string | null] {
  const d = (domain || "cheatscanner.eu").trim();
  const i = d.lastIndexOf(".");
  return i > 0 ? [d.slice(0, i), d.slice(i + 1)] : [d, null];
}

const LABELS: Record<EvidenceClass, string> = {
  NORMAL: "Normal",
  ELEVATED: "Elevated",
  HIGH: "High",
  INSUFFICIENT_DATA: "Not enough data",
};

const HINTS: Record<EvidenceClass, string> = {
  NORMAL: "No unusual behavior beyond what legitimate players show.",
  ELEVATED: "Some unusual moments worth a look. Good players and luck produce these too.",
  HIGH: "Repeated, strong unusual behavior in analyzed matches. Not a verdict.",
  INSUFFICIENT_DATA: "Too little play in analyzed matches to say anything.",
};

export function ClassBadge({ value, compact }: { value: EvidenceClass; compact?: boolean }) {
  const label = compact && value === "INSUFFICIENT_DATA" ? "No data" : LABELS[value];
  return (
    <span className={`badge badge-${value.toLowerCase()}`} title={`Evidence: ${LABELS[value]}. ${HINTS[value]}`}>
      <span className="badge-dot" />
      {label}
    </span>
  );
}

export function SideEmblem({ side, size = 18 }: { side: Side | null; size?: number }) {
  if (side === "T")
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" role="img" aria-label="T" className="emblem">
        <path d="M12 1.5l9.5 10.5L12 22.5 2.5 12z" fill="var(--t)" />
        <path d="M12 6.5c1.8 2.4 3.6 4 3.6 6.4A3.6 3.6 0 0112 16.5a3.6 3.6 0 01-3.6-3.6c0-1.2.6-2.2 1.4-3 .1 1.2.8 2 1.7 2.2-.3-2 .1-4 .5-5.6z" fill="#1a1206" />
      </svg>
    );
  if (side === "CT")
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" role="img" aria-label="CT" className="emblem">
        <path d="M12 1.5l9 3.2v6.8c0 5.4-3.8 9.4-9 11-5.2-1.6-9-5.6-9-11V4.7z" fill="var(--ct)" />
        <path d="M7 9.5l5 3.5 5-3.5M7 13.5l5 3.5 5-3.5" fill="none" stroke="#08131f" strokeWidth="1.9" strokeLinejoin="round" />
      </svg>
    );
  return <span className="emblem-none" style={{ width: size, height: size }} />;
}

const MAPS: Record<string, string> = {
  de_mirage: "Mirage", de_dust2: "Dust II", de_inferno: "Inferno", de_nuke: "Nuke", de_ancient: "Ancient",
  de_anubis: "Anubis", de_vertigo: "Vertigo", de_overpass: "Overpass", de_train: "Train",
};

export const mapName = (m: string | null | undefined) =>
  m ? (MAPS[m] ?? m.replace(/^de_|^cs_/, "").replace(/(^|_)\w/g, (c) => c.replace("_", " ").toUpperCase())) : null;

export const DISCLAIMER =
  "Evidence classes describe unusual behavior in analyzed matches. They are not a verdict and not a probability that anyone cheats.";
