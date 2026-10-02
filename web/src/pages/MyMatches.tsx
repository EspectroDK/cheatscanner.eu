import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type MatchSummary, type ShareCodeJob } from "../api";
import { MapBanner, mapName, SideEmblem } from "../art";
import { ago, ClassBadge, JOB_LABELS, JOB_RUNNING, Loading, useLoad, useUser } from "../ui";

const RUNNING: Record<string, string> = { QUEUED: "Waiting in line", PROCESSING: "Analyzing" };
const flaggedOf = (m: MatchSummary) =>
  m.players.filter((p) => p.classification && !["NORMAL", "INSUFFICIENT_DATA"].includes(p.classification));

function MatchCard({ m, me }: { m: MatchSummary; me: string }) {
  const mine = m.players.find((p) => p.steamId === me);
  const flagged = flaggedOf(m);
  const myTeam = mine?.team === 2 || mine?.team === 3 ? mine.team : null;
  const other = myTeam === 2 ? 3 : 2;
  const s = m.score;
  // Scores are keyed by the side each team started on, so the viewer's team is always shown first.
  const result = s && myTeam ? (s[myTeam] > s[other] ? "win" : s[myTeam] < s[other] ? "loss" : "tie") : null;

  return (
    <Link to={`/matches/${encodeURIComponent(m.matchId)}`} className="match-card">
      <MapBanner map={m.map} height={112}>
        <span className="map-title">{mapName(m.map)}</span>
        {s && (
          <span className="banner-score">
            {myTeam ? (
              <>
                <SideEmblem side={myTeam} size={20} />
                {s[myTeam]}
                <span className="sep">:</span>
                {s[other]}
              </>
            ) : (
              <>
                {s["3"]}
                <span className="sep">:</span>
                {s["2"]}
              </>
            )}
          </span>
        )}
      </MapBanner>
      <div className="match-card-body">
        <div className="match-card-row">
          <span className="muted small">
            {[m.mode, ago(m.playedAt ?? m.processedAt)].filter(Boolean).join(" · ")}
          </span>
          {result && <span className={`result result-${result}`}>{result}</span>}
        </div>
        {m.processingStatus !== "COMPLETED" ? (
          m.processingStatus in RUNNING ? (
            <span className="status-pill"><span className="pulse" />{RUNNING[m.processingStatus]}</span>
          ) : (
            <span className="error small">Analysis failed</span>
          )
        ) : flagged.length === 0 ? (
          <span className="muted small">Nothing unusual found</span>
        ) : (
          <div className="chips">
            {flagged.map((p) => (
              <span key={p.steamId} className="chip">
                {p.name ?? p.steamId} <ClassBadge value={p.classification} />
              </span>
            ))}
          </div>
        )}
      </div>
    </Link>
  );
}

// A match from the Steam match history that is still on its way (or failed before it got analyzed).
function PendingCard({ j, onRetry }: { j: ShareCodeJob; onRetry: (code: string) => void }) {
  return (
    <div className="match-card">
      <MapBanner map={null} height={112}>
        <span className="map-title">New match</span>
      </MapBanner>
      <div className="match-card-body">
        <div className="match-card-row">
          <span className="muted small">{["Steam match history", ago(j.createdAt)].join(" · ")}</span>
        </div>
        {JOB_RUNNING.includes(j.status) ? (
          <span className="status-pill"><span className="pulse" />{JOB_LABELS[j.status]}</span>
        ) : (
          <div className="match-card-row">
            <span className="error small">
              {JOB_LABELS[j.status] ?? j.status}
              {j.error && <span className="muted"> · {j.error}</span>}
            </span>
            {j.status === "FAILED" && <button className="button" onClick={() => onRetry(j.shareCode)}>Retry</button>}
          </div>
        )}
      </div>
    </div>
  );
}

// Users without Steam match access have no fetched matches; never let that break the page.
const shareCodes = () => api.shareCodes().catch(() => [] as ShareCodeJob[]);

export function MyMatches() {
  const me = useUser()!;
  const [poll, setPoll] = useState(0);
  const { data, error } = useLoad(api.myMatches, [poll], true);
  const jobs = useLoad(shareCodes, [poll], true);
  const busy =
    jobs.data?.some((j) => JOB_RUNNING.includes(j.status)) ||
    data?.some((m) => m.processingStatus === "QUEUED" || m.processingStatus === "PROCESSING");
  // Live progress: while something is being fetched or analyzed, refresh every few seconds so the
  // match turns into a normal card once its analysis is done.
  useEffect(() => {
    if (!busy) return;
    const t = setTimeout(() => setPoll((n) => n + 1), 4000);
    return () => clearTimeout(t);
  }, [busy, data, jobs.data]);

  async function retry(code: string) {
    try {
      await api.retryShareCode(code);
    } catch (e) {
      alert((e as Error).message);
    }
    setPoll((n) => n + 1);
  }

  if (!data) return <Loading error={error} />;
  const flagged = data.filter((m) => flaggedOf(m).length > 0).length;
  const known = new Set(data.map((m) => m.matchId));
  const pending = (jobs.data ?? []).filter(
    (j) => (JOB_RUNNING.includes(j.status) || j.status === "FAILED") && !(j.matchId && known.has(j.matchId)),
  );
  const onTheWay = pending.filter((j) => JOB_RUNNING.includes(j.status)).length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My matches</h1>
          <p className="muted">
            {data.length} analyzed match{data.length === 1 ? "" : "es"}
            {flagged > 0 && ` · ${flagged} with unusual behavior`}
            {onTheWay > 0 && ` · ${onTheWay} on the way`}
          </p>
        </div>
        <Link to="/upload" className="button">Upload a demo</Link>
      </div>
      {data.length === 0 && pending.length === 0 ? (
        <div className="empty">
          <h2>No matches yet</h2>
          <p className="muted">
            New Competitive and Premier matches show up here by themselves once Steam has them.
            You can also <Link to="/upload">upload a demo</Link>.
          </p>
        </div>
      ) : (
        <div className="match-grid">
          {pending.map((j) => <PendingCard key={j.shareCode} j={j} onRetry={retry} />)}
          {data.map((m) => <MatchCard key={m.matchId} m={m} me={me.steamId} />)}
        </div>
      )}
    </>
  );
}
