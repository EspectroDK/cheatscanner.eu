import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { DEFAULT_HOTKEYS, hotkeyFromEvent, type HotkeyName } from "../shared/hotkeys";
import type { AppState, LobbyRow } from "../shared/types";
import { bridge } from "./bridge";
import { DISCLAIMER, Logo, mapName, SideEmblem, Wordmark } from "./brand";
import { BanTag, PlayerClass } from "./parts";
import { useAppState } from "./useAppState";
import "./style.css";

function App() {
  const s = useAppState();
  if (!s) return null;
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><Logo size={26} /><Wordmark domain={s.server.domain} /></div>
        <ServerDot s={s} />
      </header>
      <main className="content">
        {s.notice && <p className="notice">{s.notice}</p>}
        <AccountCard s={s} />
        <MatchCard s={s} />
      </main>
      <footer className="footer">
        <p>{DISCLAIMER}</p>
        <SettingsPanel s={s} />
      </footer>
    </div>
  );
}

function ServerDot({ s }: { s: AppState }) {
  const [tone, label] = s.server.reachable === null ? ["pending", "Connecting…"]
    : s.server.reachable ? ["ok", "Online"] : ["bad", "Server unreachable"];
  return (
    <span className={`server-dot server-${tone}`} title={s.server.url}>
      <span className="dot" />{label}
    </span>
  );
}

// ------------------------------------------------------------------ account

function AccountCard({ s }: { s: AppState }) {
  if (s.account)
    return (
      <section className="card account">
        {s.account.avatarUrl ? <img className="avatar" src={s.account.avatarUrl} alt="" /> : <div className="avatar avatar-empty" />}
        <div className="account-text">
          <strong>{s.account.personaName ?? s.account.steamId}</strong>
          <span className="muted small">Linked to your {s.server.domain ?? "Cheatscanner"} account</span>
        </div>
        <button className="button quiet small-button" onClick={() => bridge.unlink()}>Unlink</button>
      </section>
    );
  if (s.server.authEnabled === false)
    return (
      <section className="card">
        <h2>Account</h2>
        <p className="muted small">This server runs without accounts (local use), so the app works without linking.</p>
      </section>
    );
  if (s.pairing) return <LinkingCard s={s} />;
  return (
    <section className="card">
      <h2>Link your account</h2>
      <p>
        Sign in on {s.server.domain ?? "the website"} with Steam, and the app shows the evidence class of every player
        in your match.
      </p>
      <button className="button primary" disabled={!s.server.reachable} onClick={() => bridge.startLink()}>
        Link account
      </button>
    </section>
  );
}

function LinkingCard({ s }: { s: AppState }) {
  const left = useCountdown(s.pairing!.expiresAt);
  return (
    <section className="card linking">
      <h2>Confirm in your browser</h2>
      <p className="small">
        Your browser opened {s.server.domain ?? "the website"}. Sign in with Steam and check that it shows this code:
      </p>
      <div className="code" aria-label="Link code">{s.pairing!.userCode}</div>
      <p className="muted small">
        <span className="pulse" /> Waiting for you to confirm{left ? ` · expires in ${left}` : ""}
      </p>
      <div className="row">
        <button className="button" onClick={() => bridge.openLinkPage()}>Open the page again</button>
        <button className="button quiet" onClick={() => bridge.cancelLink()}>Cancel</button>
      </div>
    </section>
  );
}

function useCountdown(iso: string): string | null {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const ms = new Date(iso).getTime() - now;
  if (!(ms > 0)) return null;
  const m = Math.floor(ms / 60_000);
  const sec = Math.floor((ms % 60_000) / 1000);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

// ------------------------------------------------------------------ match

function MatchCard({ s }: { s: AppState }) {
  const rows = s.lobby.rows;
  const title = s.match ? [mapName(s.match.map), modeName(s.match.mode)].filter(Boolean).join(" · ") || "Your match" : "Your match";
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {s.game.source === "replay" && <span className="tag" title="Playing a recorded session, not live data">Replay</span>}
      </div>
      {s.game.problem && <p className="problem small">{s.game.problem}</p>}
      {rows.length > 0 ? (
        <>
          {s.lobby.error && <p className="error small">{s.lobby.error}</p>}
          <LobbyTable rows={rows} />
          <p className="muted small hint">
            {s.overlay.mode === "none" ? null : (
              <>In game, <kbd>{s.overlay.hotkey}</kbd> shows this list and <kbd>{s.overlay.detailHotkey}</kbd> the details
                of flagged players. The overlay hides itself when the match goes live.</>
            )}
            {s.overlay.mode === "window" && (
              <button className="linkish" onClick={() => bridge.toggleOverlay()}>
                {s.overlay.visible ? "Hide overlay" : "Show overlay"}
              </button>
            )}
          </p>
          {s.game.source === "steam" && (
            <p className="muted small">
              Players come from Steam's list of people you recently played with, so teams aren't known until the match is analyzed.
            </p>
          )}
        </>
      ) : !s.game.problem ? (
        <div className="empty">
          <span className={s.game.running ? "pulse" : "idle"} />
          <p className="muted">
            {s.game.running ? "CS2 is running. The players appear here when a match loads." : "Waiting for CS2 to start."}
          </p>
        </div>
      ) : null}
    </section>
  );
}

function LobbyTable({ rows }: { rows: LobbyRow[] }) {
  const teams: [string, LobbyRow[]][] = [
    ["Counter-Terrorists", rows.filter((r) => r.side === "CT")],
    ["Terrorists", rows.filter((r) => r.side === "T")],
    [rows.some((r) => r.side) ? "Other" : "Players", rows.filter((r) => !r.side)],
  ];
  return (
    <div className="lobby">
      {teams.filter(([, list]) => list.length).map(([label, list]) => (
        <div key={label} className="team">
          <div className="team-label">{label}</div>
          {list.map((r) => <LobbyLine key={r.slot} r={r} />)}
        </div>
      ))}
    </div>
  );
}

function LobbyLine({ r }: { r: LobbyRow }) {
  return (
    <div className={`player${r.isLocal ? " is-local" : ""}`}>
      <SideEmblem side={r.side} />
      <button className="player-name" disabled={!r.steamId} title={r.steamId ? "Open on the website" : undefined}
        onClick={() => r.steamId && bridge.openPlayer(r.steamId)}>
        {r.name}{r.isLocal && <span className="you">you</span>}
      </button>
      <BanTag bans={r.bans} />
      <PlayerClass r={r} />
    </div>
  );
}

const modeName = (m: string | null) => (m ? m.charAt(0).toUpperCase() + m.slice(1).replace(/_/g, " ") : null);

// ------------------------------------------------------------------ settings

function SettingsPanel({ s }: { s: AppState }) {
  const isDefault = s.overlay.hotkey === DEFAULT_HOTKEYS.lobby && s.overlay.detailHotkey === DEFAULT_HOTKEYS.detail;
  return (
    <details className="settings">
      <summary>Settings</summary>
      {s.hotkeysEditable && (
        <>
          <div className="hotkeys small">
            <HotkeyField label="Show the player list" which="lobby" value={s.overlay.hotkey} />
            <HotkeyField label="Show details of flagged players" which="detail" value={s.overlay.detailHotkey} />
          </div>
          <div className="row small">
            <span className="muted">Click a key, then press the new one. While the app runs, CS2 doesn't get these keys.</span>
            {!isDefault && <button className="button quiet small-button" onClick={() => bridge.resetHotkeys()}>Reset</button>}
          </div>
        </>
      )}
      {s.startWithWindows !== null && (
        <label className="check small">
          <input type="checkbox" checked={s.startWithWindows} onChange={(e) => bridge.setStartWithWindows(e.target.checked)} />
          Start automatically with Windows, minimized
        </label>
      )}
      <div className="row small">
        <label className="check">
          <input type="checkbox" checked={s.overlay.siren} onChange={(e) => bridge.setSiren(e.target.checked)} />
          Siren when a High player is in my match
        </label>
        <button className="button quiet small-button" onClick={() => bridge.testSiren()}>Test</button>
      </div>
      {s.overlay.mode === "window" && (
        <p className="muted small">
          The overlay sits on top of CS2 when the game's display mode is <b>Fullscreen Windowed</b> (Settings, Video).
          In plain Fullscreen, Windows draws the game over it.
        </p>
      )}
      <p className="muted small">Version {s.version}.</p>
    </details>
  );
}

/** A hotkey button: click it, then press the new key combination (Esc cancels). */
function HotkeyField({ label, which, value }: { label: string; which: HotkeyName; value: string }) {
  const [listening, setListening] = useState(false);
  const stop = () => {
    setListening(false);
    void bridge.pauseHotkeys(false);
  };
  return (
    <div className="hotkey-row">
      <span>{label}</span>
      <button
        className={`hotkey-button${listening ? " listening" : ""}`}
        onClick={() => {
          if (listening) return stop();
          setListening(true);
          void bridge.pauseHotkeys(true);
        }}
        onBlur={() => listening && stop()}
        onKeyDown={(e) => {
          if (!listening) return;
          e.preventDefault();
          if (e.key === "Escape") return stop();
          const hotkey = hotkeyFromEvent(e);
          if (!hotkey) return; // only modifiers so far, or a key we don't offer
          setListening(false);
          // Re-registers the hotkeys: the new pair if it's usable, the old pair if not.
          void bridge.setHotkey(which, hotkey);
        }}
      >
        {listening ? "Press a key…" : <kbd>{value}</kbd>}
      </button>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
