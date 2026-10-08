// The in-game overlay: a compact, display-only window over CS2. Mouse and keyboard go straight through to
// the game. Shift+F2 shows the lobby list, F7 the extended card of flagged players. It shows itself in
// warm-up, hides when the match goes live, and plays a siren when a HIGH player is found.

import { StrictMode, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import type { AppState, AxisLevel, LobbyRow } from "../shared/types";
import { ClassBadge, Logo, mapName, SideEmblem, Wordmark } from "./brand";
import { ago, bannedOnly, BanTag, flagged, PlayerClass } from "./parts";
import { playSiren } from "./siren";
import { useAppState } from "./useAppState";
import "./style.css";

function Overlay() {
  const s = useAppState();
  useSiren(s);
  if (!s) return null;
  const detail = s.overlay.view === "detail";
  return (
    <div className="ov">
      <header className="ov-head">
        <Logo size={18} />
        <strong>{mapName(s.match?.map) ?? "Lobby"}</strong>
        <span className="ov-count">{detail ? "Flagged players" : countLine(s)}</span>
      </header>
      {detail ? <Detail s={s} /> : <Body s={s} />}
      <footer className="ov-foot">
        <span>
          {detail
            ? <><kbd>{s.overlay.detailHotkey}</kbd> hide · <kbd>{s.overlay.hotkey}</kbd> list</>
            : <><kbd>{s.overlay.hotkey}</kbd> hide · <kbd>{s.overlay.detailHotkey}</kbd> details</>}
        </span>
        <Wordmark domain={s.server.domain} />
      </footer>
    </div>
  );
}

/** Plays the siren each time the app raises a new alert (a HIGH player found in this match). */
function useSiren(s: AppState | null) {
  const seen = useRef<number | null>(null);
  const seq = s?.alert.seq ?? null;
  useEffect(() => {
    if (seq === null) return;
    if (seen.current !== null && seq > seen.current) playSiren();
    seen.current = seq;
  }, [seq]);
}

function Body({ s }: { s: AppState }) {
  if (!s.account && s.server.authEnabled !== false)
    return <p className="ov-msg">Link the app to your account in the Cheatscanner window to see classes.</p>;
  if (s.lobby.rows.length === 0) return <p className="ov-msg">The players appear here when the match loads.</p>;
  const ct = s.lobby.rows.filter((r) => r.side === "CT");
  const t = s.lobby.rows.filter((r) => r.side === "T");
  const other = s.lobby.rows.filter((r) => !r.side);
  return (
    <div className="ov-list">
      {[ct, t, other].filter((l) => l.length).map((list, i) => (
        <div key={i} className="ov-team">{list.map((r) => <Line key={r.slot} r={r} />)}</div>
      ))}
      {s.lobby.error && <p className="ov-msg ov-error">{s.lobby.error}</p>}
      {s.game.problem && <p className="ov-msg ov-note">{s.game.problem}</p>}
    </div>
  );
}

function Line({ r }: { r: LobbyRow }) {
  return (
    <div className={`ov-row${r.isLocal ? " is-local" : ""}`}>
      <SideEmblem side={r.side} size={14} />
      <span className="ov-name">{r.name}</span>
      <BanTag bans={r.bans} />
      <PlayerClass r={r} compact />
    </div>
  );
}

// ------------------------------------------------------------------ F7: flagged players

function Detail({ s }: { s: AppState }) {
  const list = flagged(s.lobby.rows);
  const banned = bannedOnly(s.lobby.rows);
  if (list.length === 0 && banned.length === 0)
    return <p className="ov-msg">{s.lobby.rows.length ? "No Elevated or High players in this match." : "The players appear here when the match loads."}</p>;
  return (
    <div className="ov-cards">
      {list.length === 0 && <p className="ov-msg">No Elevated or High players in this match.</p>}
      {list.slice(0, 2).map((r) => <Card key={r.steamId} r={r} />)}
      {list.length > 2 && <p className="ov-msg">+{list.length - 2} more flagged player{list.length > 3 ? "s" : ""}</p>}
      {banned.length > 0 && (
        <div className="ov-bans">
          <div className="ov-sub">Steam bans on record, account-level</div>
          <div className="ov-recent">
            {banned.map((r) => (
              <div key={r.slot} className="ov-recent-row">
                <span className="ov-name">{r.name}</span>
                <BanTag bans={r.bans} />
                <span className="muted">{ago(r.bans!.lastBanOn)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const AXES: [keyof NonNullable<LobbyRow["detail"]>["axes"], string][] = [
  ["wallTracking", "Wall tracking"],
  ["aim", "Aim anomaly"],
  ["reaction", "Reaction anomaly"],
];

function Card({ r }: { r: LobbyRow }) {
  const d = r.detail!;
  return (
    <section className={`ov-card tone-${r.classification!.toLowerCase()}`}>
      <div className="ov-card-head">
        <span className="ov-name">{r.name}</span>
        <span className="ov-score" title="Evidence score from all analyzed matches. Not a probability of cheating.">
          <b>{d.evidenceScore}</b> / 100
        </span>
      </div>
      <div className="ov-card-class"><ClassBadge value={r.classification!} compact /> <BanTag bans={r.bans} /></div>
      <dl className="ov-facts">
        <dt>Matches analysed</dt><dd>{r.matchesAnalyzed}</dd>
        <dt>High-risk matches</dt><dd>{d.highEvidenceMatches}</dd>
        {AXES.map(([k, label]) => (
          <FactLevel key={k} label={label} level={d.axes[k]} />
        ))}
      </dl>
      {d.recent.length > 0 && (
        <>
          <div className="ov-sub">Recent evidence</div>
          <div className="ov-recent">
            {d.recent.map((m, i) => (
              <div key={i} className="ov-recent-row">
                <span>{mapName(m.map) ?? "Unknown map"}</span>
                <span>{m.evidenceScore}/100</span>
                <span className="muted">{ago(m.playedAt)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function FactLevel({ label, level }: { label: string; level: AxisLevel }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={`lvl lvl-${level.toLowerCase()}`}>{level}</dd>
    </>
  );
}

function countLine(s: AppState): string {
  const known = s.lobby.rows.filter((r) => r.status === "ok" && r.classification && r.classification !== "INSUFFICIENT_DATA").length;
  return s.lobby.rows.length ? `${known} of ${s.lobby.rows.length} known` : "";
}

const root = document.getElementById("root");
if (root)
  createRoot(root).render(
    <StrictMode>
      <Overlay />
    </StrictMode>,
  );
