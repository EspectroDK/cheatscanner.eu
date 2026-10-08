import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { EvidenceEvent } from "./api";
import { mapName } from "./art";
import { ago, pct } from "./ui";

const TICK_RATE = 64;

// Jump a few seconds before the flagged moment so the lead-up is visible.
const gotoCommand = (tick: number) => `demo_gototick ${Math.max(0, tick - 5 * TICK_RATE)}`;

// Clips are rendered at clip_fps (config/default_config.toml), so one step moves exactly one frame.
const CLIP_FPS = 32;
const SPEEDS = [0.25, 0.5, 1];

// The chosen speed carries over to every clip while the page is open.
let rememberedSpeed = 1;

function ClipPlayer({ src, poster }: { src: string; poster?: string }) {
  const video = useRef<HTMLVideoElement>(null);
  const [speed, setSpeedState] = useState(rememberedSpeed);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    // defaultPlaybackRate survives the browser resetting playbackRate when the video loads.
    v.defaultPlaybackRate = speed;
    v.playbackRate = speed;
  }, [speed]);

  function setSpeed(s: number) {
    rememberedSpeed = s;
    setSpeedState(s);
  }

  function step(frames: number) {
    const v = video.current;
    if (!v) return;
    v.pause();
    const end = Number.isFinite(v.duration) ? v.duration : Infinity;
    v.currentTime = Math.min(end, Math.max(0, v.currentTime + frames / CLIP_FPS));
  }

  return (
    <div className="clip-player">
      <video ref={video} src={src} poster={poster} controls preload="metadata" playsInline className="clip" />
      <div className="clip-controls">
        <span className="muted small">Speed</span>
        {SPEEDS.map((s) => (
          <button
            key={s}
            className={`button small-button${s === speed ? " on" : ""}`}
            aria-pressed={s === speed}
            onClick={() => setSpeed(s)}
          >
            {s}×
          </button>
        ))}
        <span className="clip-controls-gap" />
        <button className="button small-button" onClick={() => step(-1)} title="Pause and go back one frame">
          ◀ Frame
        </button>
        <button className="button small-button" onClick={() => step(1)} title="Pause and go forward one frame">
          Frame ▶
        </button>
      </div>
    </div>
  );
}

function WatchInGame({ tick }: { tick: number }) {
  const [copied, setCopied] = useState(false);
  const cmd = gotoCommand(tick);
  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the command is still shown */
    }
  }
  return (
    <div className="watch">
      <span className="muted small">Watch in CS2: open this match from the Watch tab, then paste in the console</span>
      <code>{cmd}</code>
      <button className="button small-button" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
    </div>
  );
}

export function EvidenceCard({ e, names, showMatch }: { e: EvidenceEvent; names?: Record<string, string>; showMatch?: boolean }) {
  const [showPlot, setShowPlot] = useState(false);
  return (
    <article className="evidence">
      <header>
        <strong>{e.detector.replace(/_/g, " ")}</strong>
        {names && <span> · {names[e.steamId] ?? e.steamId}</span>}
        <span className="muted small">
          {" "}· round {e.round ?? "–"} · confidence {pct(e.confidence)}
          {showMatch &&
            (e.matchVisible === false || !e.matchId ? (
              <> · {mapName(e.map)}, {ago(e.playedAt ?? null)} · a match you weren't in</>
            ) : (
              <> · <Link to={`/matches/${encodeURIComponent(e.matchId)}`}>{e.map ? mapName(e.map) : "match"}</Link></>
            ))}
        </span>
      </header>
      {e.explanation && <p className="small">{e.explanation}</p>}
      {e.targetName && (
        <p className="small muted">
          Other player:{" "}
          {e.targetSteamId ? <Link to={`/players/${e.targetSteamId}`}>{e.targetName}</Link> : <span>{e.targetName}</span>}
        </p>
      )}
      {e.clipUrl ? (
        // Reconstruction from demo data, not game footage: the outlines are reviewer annotations.
        <ClipPlayer src={e.clipUrl} poster={e.posterUrl ?? undefined} />
      ) : e.clipPending ? (
        <div className="clip-missing small muted">The clip for this event is being made and appears here when it is ready.</div>
      ) : (
        <div className="clip-missing small muted">
          No video for this event. Clips are made for a match's strongest events, on maps whose geometry we have; at
          times only for players rated Elevated or High in that match.
        </div>
      )}
      {e.plotUrl && (
        <p>
          <button className="button small-button" onClick={() => setShowPlot((v) => !v)}>
            {showPlot ? "Hide chart" : "Show chart"}
          </button>
        </p>
      )}
      {showPlot && e.plotUrl && <img src={e.plotUrl} alt="Aim and visibility over time around this event" className="plot" />}
      {e.matchVisible !== false && <WatchInGame tick={e.tickPeak} />}
    </article>
  );
}

export function EvidenceList({ events, names, showMatch }: { events: EvidenceEvent[]; names?: Record<string, string>; showMatch?: boolean }) {
  if (events.length === 0) return <p className="muted">No evidence events.</p>;
  return (
    <>
      <p className="muted small">
        Clips are reconstructions from the demo and the map geometry, not game footage. Enemy outlines are drawn for
        review (dashed when the enemy was hidden from the player).
      </p>
      {events.map((e) => (
        <EvidenceCard key={e.id} e={e} names={names} showMatch={showMatch} />
      ))}
    </>
  );
}
