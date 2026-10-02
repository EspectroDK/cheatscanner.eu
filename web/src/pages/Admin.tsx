import { useEffect, useState } from "react";
import { api, type AdminOverview } from "../api";
import { ago, ClassBadge, Loading, useLoad } from "../ui";

const REFRESH_MS = 15_000;

function duration(s: number | null | undefined): string {
  if (s == null) return "–";
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${(s / 60).toFixed(1)} min`;
  if (s < 86400 * 2) return `${(s / 3600).toFixed(1)} h`;
  return `${(s / 86400).toFixed(1)} d`;
}

const n = (x: number) => x.toLocaleString();

function Stat({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: "warn" | "bad" }) {
  return (
    <div className={`stat${tone ? ` stat-${tone}` : ""}`}>
      <div className="label">{label}</div>
      <div className="value">{typeof value === "number" ? n(value) : value}</div>
      {sub && <div className="muted small">{sub}</div>}
    </div>
  );
}

/** Matches analyzed per day, last 14 days. One series, so no legend; hover shows the day and count. */
function PerDay({ days }: { days: AdminOverview["matches"]["perDay"] }) {
  const max = Math.max(1, ...days.map((d) => d.count));
  const label = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return (
    <figure className="per-day" aria-label="Matches analyzed per day">
      <div className="per-day-bars">
        {days.map((d) => (
          <div key={d.date} className="per-day-col" data-tip={`${label(d.date)}: ${d.count} ${d.count === 1 ? "match" : "matches"}`}>
            <span style={{ height: `${(d.count / max) * 100}%` }} className={d.count ? "" : "zero"} />
          </div>
        ))}
      </div>
      <figcaption className="per-day-axis muted small">
        <span>{label(days[0].date)}</span>
        <span>max {n(max)} per day</span>
        <span>{label(days[days.length - 1].date)}</span>
      </figcaption>
    </figure>
  );
}

export function Admin() {
  const [tick, setTick] = useState(0);
  const data = useLoad(api.adminOverview, [tick], true);
  useEffect(() => {
    const t = setTimeout(() => setTick((x) => x + 1), REFRESH_MS);
    return () => clearTimeout(t);
  }, [tick]);

  if (!data.data) return <Loading error={data.error} />;
  const o = data.data;
  const f = o.fetch.byStatus;
  const valveWaiting = f.QUEUED + f.FETCHING;
  const fetcherQuiet = !o.system.fetcherLastSeenAt || Date.now() - new Date(o.system.fetcherLastSeenAt).getTime() > 5 * 60_000;
  const since = `recorded since ${new Date(o.speed.recordedSince).toLocaleDateString()}`;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Administration</h1>
          <p className="muted small">
            Updated {ago(o.generatedAt)}, refreshes every {REFRESH_MS / 1000} s.
            {data.error && <span className="error"> Last refresh failed: {data.error}</span>}
          </p>
        </div>
      </div>

      <h2>Right now</h2>
      <div className="stat-grid">
        <Stat label="Waiting for analysis" value={o.live.queued}
              sub={o.live.queued ? `oldest waiting ${duration(o.live.oldestWaitingSeconds)}` : "queue is empty"}
              tone={o.live.queued > 5 ? "warn" : undefined} />
        <Stat label="Being analyzed" value={`${o.live.processing} / ${o.live.workers}`} sub="demos / analysis workers" />
        <Stat label="Waiting for Valve" value={valveWaiting}
              sub={o.fetch.oldestQueuedAt ? `oldest queued ${ago(o.fetch.oldestQueuedAt)}` : "fetched matches"} />
        <Stat label="Downloading" value={f.DOWNLOADING} sub={`${o.live.receivingUploads} upload(s) arriving`} />
        <Stat label="Demo fetcher" value={o.system.fetcherLastSeenAt ? ago(o.system.fetcherLastSeenAt) : "not seen"}
              sub="since this server started" tone={fetcherQuiet ? "warn" : undefined} />
        <Stat label="Disk free" value={`${n(o.system.diskFreeGb)} GB`} sub={`of ${n(o.system.diskTotalGb)} GB`}
              tone={o.system.diskFreeGb < 20 ? "bad" : undefined} />
      </div>

      <h2>Analysis speed</h2>
      <div className="stat-grid">
        <Stat label="Analyzed, last 24 h" value={o.matches.analyzed24h} sub={`${n(o.matches.analyzed7d)} in 7 days`} />
        <Stat label="Time per match" value={duration(o.speed.analysisSeconds.median)}
              sub={o.speed.analysisSeconds.count ? `median; slowest 10% over ${duration(o.speed.analysisSeconds.p90)}` : since} />
        <Stat label="Wait before analysis" value={duration(o.speed.queueWaitSeconds.median)}
              sub={o.speed.queueWaitSeconds.count ? "median" : since} />
        <Stat label="Found to analyzed" value={duration(o.speed.fetchedToResultSeconds.median)}
              sub={o.speed.fetchedToResultSeconds.count ? "median, fetched matches, 30 days" : "no fetched matches in 30 days"} />
      </div>
      <PerDay days={o.matches.perDay} />

      <h2>Users and overlay</h2>
      <div className="stat-grid">
        <Stat label="Unique users" value={o.users.total} sub={`${n(o.users.new7d)} new in 7 days, ${n(o.users.new30d)} in 30`} />
        <Stat label="Active users" value={o.users.active24h} sub={`last 24 h; ${n(o.users.active7d)} in 7 d, ${n(o.users.active30d)} in 30 d`} />
        <Stat label="Match history connected" value={o.users.matchHistoryConnected}
              sub={o.fetch.lastHistoryCheckAt ? `last checked ${ago(o.fetch.lastHistoryCheckAt)}` : "never checked"} />
        <Stat label="Overlays in use" value={o.companion.activeLast15m}
              sub={`last 15 min; ${n(o.companion.active24h)} in 24 h, ${n(o.companion.active7d)} in 7 d`} />
        <Stat label="Linked overlay apps" value={o.companion.linkedApps} sub={`${n(o.companion.linkedUsers)} user(s)`} />
        <Stat label="Lobby lookups, 24 h" value={o.companion.lookups24h}
              sub={`${n(o.companion.lookupUsers24h)} user(s); ${n(o.companion.lookups7d)} in 7 d`} />
        <Stat label="Uploads started, 24 h" value={o.uploads.attempts24h} sub={`${n(o.uploads.attempts7d)} in 7 days`} />
      </div>

      <h2>All time</h2>
      <div className="stat-grid">
        <Stat label="Matches analyzed" value={o.matches.analyzed}
              sub={`${n(o.matches.bySource.fetched)} fetched, ${n(o.matches.bySource.uploaded)} uploaded, ${n(o.matches.bySource.other)} other`} />
        <Stat label="Failed analyses" value={o.matches.failed} tone={o.matches.failed ? "warn" : undefined} />
        <Stat label="Players analyzed" value={o.players.total} sub={`${n(o.players.assessed)} with a class`} />
        <Stat label="Evidence events" value={o.players.evidenceEvents} />
      </div>
      <div className="admin-two">
        <div className="card-table">
          <table>
            <thead>
              <tr>
                <th>Players by class</th>
                <th className="num-col" title="Class from the player's whole history. The play pattern only counts from 2 matches on.">Overall</th>
                <th className="num-col" title="Each player counted once, at their highest class in any single match.">Highest in a match</th>
              </tr>
            </thead>
            <tbody>
              {(["NORMAL", "ELEVATED", "HIGH", "INSUFFICIENT_DATA"] as const).map((c) => (
                <tr key={c}>
                  <td><ClassBadge value={c} /></td>
                  <td className="num-col">{n(o.players.byClass[c])}</td>
                  <td className="num-col">{n(o.players.byHighestMatchClass[c])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card-table">
          <table>
            <thead><tr><th>Map</th><th className="num-col">Matches</th></tr></thead>
            <tbody>
              {o.matches.byMap.length === 0 && <tr><td className="muted" colSpan={2}>No matches yet</td></tr>}
              {o.matches.byMap.map((m) => (
                <tr key={m.map}><td>{m.map}</td><td className="num-col">{n(m.count)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <h2>Fetched matches</h2>
      <div className="card-table table-wrap">
        <table>
          <thead>
            <tr>{(["QUEUED", "FETCHING", "DOWNLOADING", "ANALYZING", "DONE", "FAILED", "EXPIRED"] as const).map((s) => <th key={s} className="num-col">{s.toLowerCase()}</th>)}</tr>
          </thead>
          <tbody>
            <tr>{(["QUEUED", "FETCHING", "DOWNLOADING", "ANALYZING", "DONE", "FAILED", "EXPIRED"] as const).map((s) => <td key={s} className="num-col">{n(f[s])}</td>)}</tr>
          </tbody>
        </table>
      </div>
      {o.fetch.recentProblems.length > 0 && (
        <>
          <h2>Latest fetch problems</h2>
          <div className="card-table table-wrap">
            <table>
              <tbody>
                {o.fetch.recentProblems.map((p) => (
                  <tr key={p.shareCode}>
                    <td className="small"><code>{p.shareCode}</code></td>
                    <td className="small">{p.status.toLowerCase()}</td>
                    <td className="muted small">{p.error ?? ""}</td>
                    <td className="muted small">{ago(p.updatedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {o.maps && o.maps.length > 0 && (
        <>
          <h2>Map meshes</h2>
          {o.maps.some((m) => m.stale || m.missing) && (
            <p className="small">
              <span className="error">Some maps need a new mesh.</span> Build them from your updated game with
              tools/geometry/build_tris.py, then run tools/deploy/push-maps.ps1.
            </p>
          )}
          <div className="card-table table-wrap">
            <table>
              <thead>
                <tr><th>Map</th><th>Mesh</th><th className="num-col">Mesh patch</th><th className="num-col">Newest demo patch</th><th>Clip mesh</th><th>Updated</th></tr>
              </thead>
              <tbody>
                {o.maps.map((m) => (
                  <tr key={m.map}>
                    <td>{m.map}</td>
                    <td className={m.missing || m.stale ? "error" : ""}>{m.missing ? "missing" : m.stale ? "out of date" : "ok"}</td>
                    <td className="num-col">{m.meshPatch ?? "–"}</td>
                    <td className="num-col">{m.latestDemoPatch ?? "–"}</td>
                    <td className="muted small">{m.renderMesh ? "game-built" : "same as analysis"}</td>
                    <td className="muted small">{m.meshUpdatedAt ? ago(m.meshUpdatedAt) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <p className="muted small">
        Time per match, wait before analysis and lobby lookups are only recorded from{" "}
        {new Date(o.speed.recordedSince).toLocaleDateString()} on. "Overlays in use" counts linked apps that talked to
        the server in the period.
      </p>
    </>
  );
}
