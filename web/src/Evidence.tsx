import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Classification, EvidenceEvent } from "./api";
import { mapName } from "./art";
import { detectorSummary, detectorTitle } from "./detectors";
import { ago, ClassBadge, pct } from "./ui";

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
      {/* With a poster nothing is downloaded until play: a match page can hold many clips. */}
      <video ref={video} src={src} poster={poster} controls preload={poster ? "none" : "metadata"} playsInline className="clip" />
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
  const summary = detectorSummary(e.detector);
  return (
    <article className="evidence">
      <header>
        <strong>{detectorTitle(e.detector)}</strong>
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
      {summary ? (
        <>
          <p className="small">{summary}</p>
          {e.explanation && (
            <details className="small evidence-details">
              <summary>Details</summary>
              <p>{e.explanation}</p>
            </details>
          )}
        </>
      ) : (
        e.explanation && <p className="small">{e.explanation}</p>
      )}
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

const LEVEL: Record<string, number> = { HIGH: 3, VERY_HIGH: 3, ELEVATED: 2, NORMAL: 1, INSUFFICIENT_DATA: 0 };
const raised = (c: Classification | null | undefined) => (LEVEL[c ?? ""] ?? 0) >= 2;

const CLIP_NOTE = (
  <p className="muted small">
    Clips are reconstructions from the demo and the map geometry, not game footage. Enemy outlines are drawn for
    review (dashed when the enemy was hidden from the player).
  </p>
);

/**
 * Evidence of one or more players. With ``classes`` (a match page) the events are grouped per player, players
 * with a raised class first; the moments of players whose class stayed Normal are folded away, so a weak moment
 * doesn't look as heavy as the evidence behind an Elevated or High class.
 */
export function EvidenceList({ events, names, showMatch, classes }: {
  events: EvidenceEvent[];
  names?: Record<string, string>;
  showMatch?: boolean;
  classes?: Record<string, Classification | null | undefined>;
}) {
  if (events.length === 0) return <p className="muted">No evidence events.</p>;
  if (!classes)
    return (
      <>
        {CLIP_NOTE}
        {events.map((e) => <EvidenceCard key={e.id} e={e} names={names} showMatch={showMatch} />)}
      </>
    );

  const groups = new Map<string, EvidenceEvent[]>();
  for (const e of events) groups.set(e.steamId, [...(groups.get(e.steamId) ?? []), e]);
  const order = [...groups.keys()].sort(
    (a, b) => (LEVEL[classes[b] ?? ""] ?? 0) - (LEVEL[classes[a] ?? ""] ?? 0) || groups.get(b)!.length - groups.get(a)!.length,
  );
  const name = (sid: string) => names?.[sid] ?? sid;
  return (
    <>
      {CLIP_NOTE}
      {order.map((sid) => {
        const list = groups.get(sid)!;
        const cards = list.map((e) => <EvidenceCard key={e.id} e={e} names={names} showMatch={showMatch} />);
        const count = `${list.length} moment${list.length === 1 ? "" : "s"}`;
        return raised(classes[sid]) ? (
          <section key={sid} className="evidence-group">
            <h3 className="evidence-group-head">{name(sid)} <ClassBadge value={classes[sid]} /> <span className="muted small">{count}</span></h3>
            {cards}
          </section>
        ) : (
          <details key={sid} className="evidence-group evidence-folded">
            <summary>
              <strong>{name(sid)}</strong> <ClassBadge value={classes[sid]} />{" "}
              <span className="muted small">
                {count}, not enough on {list.length === 1 ? "its" : "their"} own for a raised class
              </span>
            </summary>
            {cards}
          </details>
        );
      })}
    </>
  );
}
