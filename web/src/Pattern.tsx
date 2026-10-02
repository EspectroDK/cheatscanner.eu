import type { PatternBreakdown } from "./api";
import { mapName } from "./art";
import { ClassBadge } from "./ui";

// The numbers behind the play-pattern score (scoring/player_evidence.py): per match how the pattern ranks
// among clean players, and per measurement the player's typical value next to clean players and the
// value ranges where labelled cheaters are more common.

type Spec = { name: string; what: string; noun: string; fmt: (v: number) => string };
const pctFmt = (v: number) => `${Math.round(100 * v)}%`;

export const MEASUREMENTS: Record<string, Spec> = {
  "trigger_timing.trigger_ms": {
    name: "Reaction time", what: "from an enemy under the crosshair to the shot", noun: "shots",
    fmt: (v) => `${Math.round(v)} ms`,
  },
  "hidden_tracking.mean_error_deg": {
    name: "Aim at enemies behind walls", what: "average angle between crosshair and a hidden enemy", noun: "moments",
    fmt: (v) => `${Math.round(v)}°`,
  },
  "hidden_tracking.frac_within_2deg": {
    name: "Time on enemies behind walls", what: "share of time within 2° of a hidden enemy", noun: "moments",
    fmt: pctFmt,
  },
  "mechanical_impossibility.discrepancy_deg": {
    name: "Hit offset", what: "angle between where the crosshair pointed and where the hit landed", noun: "hits",
    fmt: (v) => `${v.toFixed(2)}°`,
  },
  "aim_acquisition.peak_jerk_deg_s3": {
    name: "Aim snap", what: "how abruptly the aim moves onto a target", noun: "aim moves",
    fmt: (v) => `${Math.round(v / 1000)}k°/s³`,
  },
  "input_lattice_summary.lattice_fit": {
    name: "Mouse-step fit", what: "share of calm view changes that are whole mouse steps", noun: "matches",
    fmt: pctFmt,
  },
};

function range([lo, hi]: (number | null)[], fmt: (v: number) => string) {
  if (lo == null && hi != null) return `below ${fmt(hi)}`;
  if (hi == null && lo != null) return `above ${fmt(lo)}`;
  return `${fmt(lo ?? 0)} to ${fmt(hi ?? 0)}`;
}

function position(p: number, noun: string) {
  if (p >= 0.9) return `higher than 90% or more of clean players' ${noun}`;
  if (p <= 0.1) return `lower than 90% or more of clean players' ${noun}`;
  return p >= 0.5
    ? `higher than ${Math.round(100 * p)}% of clean players' ${noun}`
    : `lower than ${Math.round(100 * (1 - p))}% of clean players' ${noun}`;
}

export function Pattern({ data }: { data: PatternBreakdown }) {
  const scored = data.matches.filter((m) => m.cleanPercentile != null);
  if (!scored.some((m) => (m.strength ?? 0) > 0)) return null;
  const ref = data.reference;
  return (
    <section className="panel">
      <h2>Play pattern</h2>
      <p className="muted small">
        The play pattern combines the measurements below over every duel of a match into one score, and compares it
        with {ref?.cleanPlayers ?? "the"} clean players
        {ref?.cleanMatches ? ` from ${ref.cleanMatches} matches` : ""} of a labelled research dataset. The cheater
        ranges come from labelled cheaters in the same dataset. These are statistics, not proof.
      </p>
      <ul className="small">
        {scored.slice(-5).map((m, i) => (
          <li key={i}>
            {mapName(m.map)}, {m.playedAt ? new Date(m.playedAt).toLocaleDateString() : "unknown date"}: pattern more
            unusual than <strong>{(100 * (m.cleanPercentile ?? 0)).toFixed(1)}%</strong> of clean players{" "}
            <ClassBadge value={m.classification} />
          </li>
        ))}
      </ul>
      {data.features.length === 0 ? (
        <p className="muted small">The per-measurement numbers aren't available for these matches.</p>
      ) : (
        <div className="pattern-list">
          {data.features.map((f) => {
            const s = MEASUREMENTS[f.feature];
            if (!s) return null;
            return (
              <div key={f.feature} className={`pattern-row${f.contribution > 0 ? "" : " muted"}`}>
                <div>
                  <strong>{s.name}</strong>
                  <div className="small muted">{s.what}</div>
                  <div className="small">{f.contribution > 0 ? "Pushes the pattern up" : "Looks normal"}</div>
                </div>
                <div>
                  <div className="pattern-label">This player</div>
                  {s.fmt(f.value)} typical
                  <div className="small muted">{f.n} {s.noun}</div>
                </div>
                <div>
                  <div className="pattern-label">Clean players</div>
                  {f.cleanMedian != null && f.cleanP10 != null && f.cleanP90 != null ? (
                    <>
                      {s.fmt(f.cleanMedian)} typical, 80% between {s.fmt(f.cleanP10)} and {s.fmt(f.cleanP90)}
                      {f.cleanPercentile != null && (
                        <div className="small">This player's typical value is {position(f.cleanPercentile, s.noun)}.</div>
                      )}
                    </>
                  ) : (
                    <span className="muted">No reference range</span>
                  )}
                </div>
                <div>
                  <div className="pattern-label">Where cheaters are more common</div>
                  {f.cheaterRanges.length ? f.cheaterRanges.map((r) => range(r, s.fmt)).join(", ") : "–"}
                  {f.cheaterRanges.length > 0 && (
                    <div className="small">
                      {pctFmt(f.cheaterShare)} of their {s.noun}
                      {f.cleanShare != null ? `, ${pctFmt(f.cleanShare)} of clean players'` : ""}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
