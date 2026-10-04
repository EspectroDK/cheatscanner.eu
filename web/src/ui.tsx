import { createContext, useContext, useEffect, useState } from "react";
import type { Classification, ShareCodeJob, User } from "./api";

// The public source code; the AGPL asks every deployment to offer its source to its users.
export const SOURCE_URL = "https://github.com/EspectroDK/cheatscanner.eu";

export const UserContext = createContext<User | null>(null);
export const useUser = () => useContext(UserContext);

// Steps of a match fetched from the user's Steam match history (Settings and My matches).
export const JOB_LABELS: Record<ShareCodeJob["status"], string> = {
  QUEUED: "Waiting",
  FETCHING: "Asking Valve for the demo",
  DOWNLOADING: "Downloading",
  ANALYZING: "Analyzing",
  DONE: "Analyzed",
  SKIPPED: "Not analyzed (not Premier or Competitive)",
  FAILED: "Failed",
  EXPIRED: "Demo no longer available",
};
export const JOB_RUNNING: ShareCodeJob["status"][] = ["QUEUED", "FETCHING", "DOWNLOADING", "ANALYZING"];

// Evidence classes, never "cheater" or a probability (spec section 22).
const LABELS: Record<string, string> = {
  NORMAL: "Normal",
  ELEVATED: "Elevated",
  HIGH: "High",
  VERY_HIGH: "High",
  INSUFFICIENT_DATA: "Not enough data",
};

const HINTS: Record<string, string> = {
  NORMAL: "No unusual behavior beyond what legitimate players show.",
  ELEVATED: "Some unusual moments worth a look. Good players and luck produce these too.",
  HIGH: "Repeated, strong unusual behavior. Review the evidence before drawing conclusions.",
  VERY_HIGH: "Repeated, strong unusual behavior. Review the evidence before drawing conclusions.",
  INSUFFICIENT_DATA: "Too little play in analyzed matches to say anything.",
};

export function ClassBadge({ value, size }: { value: Classification | null | undefined; size?: "lg" }) {
  const v = value ?? "INSUFFICIENT_DATA";
  const tone = v === "VERY_HIGH" ? "HIGH" : v;
  return (
    <span className={`badge badge-${tone.toLowerCase()}${size ? " badge-lg" : ""}`} title={`Evidence: ${LABELS[v] ?? v}. ${HINTS[v] ?? ""}`}>
      <span className="badge-dot" />
      {LABELS[v] ?? v}
    </span>
  );
}

// Premier ratings are shown with CS2's colour tiers; other rank types are left out rather than guessed.
export function Rank({ type, value }: { type: number | null; value: number | null }) {
  if (type !== 11 || !value) return <span className="muted">–</span>;
  const tier = value >= 30000 ? 7 : value >= 25000 ? 6 : value >= 20000 ? 5 : value >= 15000 ? 4 : value >= 10000 ? 3 : value >= 5000 ? 2 : 1;
  return <span className={`rating rating-${tier}`}>{value.toLocaleString()}</span>;
}

// keep: leave the previous data on screen while reloading (used for live polling, avoids flicker).
export function useLoad<T>(load: () => Promise<T>, deps: unknown[], keep = false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    if (!keep) setData(null);
    setError(null);
    load().then(
      (d) => live && setData(d),
      (e) => live && setError(e.message ?? String(e)),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error };
}

export function Loading({ error }: { error: string | null }) {
  return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
}

export const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "unknown date");

export function ago(iso: string | null): string {
  if (!iso) return "unknown date";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString();
}

export const pct = (x: number | null | undefined) => (x == null ? "–" : x.toFixed(2));

export const TEAM: Record<number, string> = { 2: "T", 3: "CT" };
