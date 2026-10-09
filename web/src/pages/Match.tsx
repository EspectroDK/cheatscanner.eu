import { type ReactNode, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ClipStatus, type EvidenceEvent, type MatchDetail, type ShareLink } from "../api";
import { MapBanner, mapName, SIDE_NAME, SideEmblem } from "../art";
import { EvidenceList } from "../Evidence";
import { BanBadge, ClassBadge, Loading, matchStatusText, modeName, pct, Rank, useLoad, useUser, when } from "../ui";

type Team = 2 | 3;
const other = (t: Team): Team => (t === 2 ? 3 : 2);

function Rounds({ rounds }: { rounds: MatchDetail["rounds"] }) {
  if (rounds.length === 0) return null;
  return (
    <div className="rounds" aria-label="Round history">
      {rounds.map((r) => (
        <span key={r.round} style={{ display: "contents" }}>
          {(r.round === 13 || (r.round > 24 && (r.round - 25) % 3 === 0)) && <span className="round-half" title="Sides swap" />}
          <span
            className={`round${r.winner === 2 || r.winner === 3 ? ` w${r.winner}` : ""}`}
            title={
              r.winner === 2 || r.winner === 3
                ? `Round ${r.round}: won by ${SIDE_NAME[r.winner]}` +
                  (r.winnerTeam ? ` (team that started ${SIDE_NAME[r.winnerTeam]})` : "")
                : `Round ${r.round}`
            }
          >
            {r.round}
          </span>
        </span>
      ))}
      <span className="rounds-legend">
        <span><SideEmblem side={2} size={14} /> T won</span>
        <span><SideEmblem side={3} size={14} /> CT won</span>
      </span>
    </div>
  );
}

function TeamTable({ team, data, me, won }: { team: number | null; data: MatchDetail; me: string; won: number | null }) {
  const rounds = data.rounds.length || 1;
  const players = data.players.filter((p) => p.team === team).sort((a, b) => b.damage - a.damage);
  return (
    <section className="team-block">
      <div className={`team-head side-${team}`}>
        <SideEmblem side={team} />
        {team === 2 || team === 3 ? `Started as ${SIDE_NAME[team]}` : "Players"}
        {won != null && <span className="won muted">{won} rounds won</span>}
      </div>
      <div className="table-wrap">
        <table className="scoreboard">
          <thead>
            <tr>
              <th>Player</th>
              <th className="opt">Premier</th>
              <th className="num-col">K</th>
              <th className="num-col">D</th>
              <th className="num-col opt">A</th>
              <th className="num-col opt">ADR</th>
              <th className="num-col opt">HS%</th>
              <th>Evidence</th>
              <th className="num-col">Events</th>
            </tr>
          </thead>
          <tbody>
            {players.map((p) => (
              <tr key={p.steamId}>
                <td>
                  <span className="player-cell">
                    {(p.linkable ?? p.visible) ? (
                      <Link to={`/players/${p.steamId}`}>{p.name ?? p.steamId}</Link>
                    ) : (
                      <span className="name">{p.name ?? p.steamId}</span>
                    )}
                    {p.steamId === me && <span className="you">YOU</span>}
                    <BanBadge bans={p.bans} playedAt={data.playedAt} />
                  </span>
                </td>
                <td className="opt"><Rank type={p.rankType} value={p.rankNew} /></td>
                <td className="num-col">{p.kills}</td>
                <td className="num-col">{p.deaths}</td>
                <td className="num-col opt">{p.assists}</td>
                <td className="num-col opt">{Math.round(p.damage / rounds)}</td>
                <td className="num-col opt">{p.kills ? `${Math.round((100 * p.headshots) / p.kills)}%` : "–"}</td>
                {p.visible ? (
                  <>
                    <td>
                      <ClassBadge value={p.assessment?.classification} />
                    </td>
                    <td
                      className="num-col"
                      title={
                        `Evidence score ${pct(p.assessment?.overallEvidenceScore)}` +
                        ((p.assessment?.profileStrength ?? 0) > 0 ? ". Includes an unusual overall play pattern, which has no events." : "")
                      }
                    >
                      {p.assessment?.evidenceEventCount ?? 0}
                      {(p.assessment?.profileStrength ?? 0) > 0 && <span className="muted"> +pattern</span>}
                    </td>
                  </>
                ) : (
                  <>
                    <td className="muted small" title="Only shown for players you have played with or against, or whose match you uploaded">Hidden</td>
                    <td className="num-col muted">–</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// Opens CS2 through Steam; the game downloads the match from Valve by its share code and plays it.
const watchInCs2 = (shareCode: string) =>
  `steam://rungame/730/76561202255233023/+csgo_download_match%20${encodeURIComponent(shareCode)}`;

function ValveDemo({ demo }: { demo: NonNullable<MatchDetail["valveDemo"]> }) {
  const file = demo.url.split("/").pop()?.replace(/\.bz2$/, "") ?? "";
  const until = new Date(demo.availableUntil).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return (
    <div className="valve-demo">
      <a className="button primary" href={watchInCs2(demo.shareCode)}>
        Watch in CS2
      </a>
      <a className="button" href={demo.url} rel="noreferrer noopener">
        Download demo
      </a>
      <span className="muted small">Valve keeps this demo until about {until}.</span>
      <details>
        <summary>If CS2 doesn't start the demo</summary>
        <ol className="small">
          <li>Use Download demo and unpack the <code>.dem.bz2</code> file (for example with 7-Zip).</li>
          <li>
            Put <code>{file}</code> in <code>Counter-Strike Global Offensive\game\csgo</code> in your Steam library.
          </li>
          <li>
            In CS2, open the console and type <code>playdemo {file.replace(/\.dem$/, "")}</code>.
          </li>
        </ol>
      </details>
    </div>
  );
}

const CLIP_REFRESH_MS = 30_000;

function aboutMinutes(seconds: number): string {
  const min = Math.max(1, Math.round(seconds / 60));
  if (min < 60) return `about ${min} min`;
  const h = Math.floor(min / 60);
  const m = Math.round((min % 60) / 5) * 5;
  return `about ${h} h${m ? ` ${m} min` : ""}`;
}

/** Results come first; the evidence clips are rendered afterwards, and this says when to expect them. */
function ClipsNotice({ clips }: { clips: ClipStatus }) {
  if (clips.state === "DONE" || clips.pending === 0) return null;
  if (clips.state === "FAILED")
    return <p className="notice">Some evidence clips of this match could not be made. The results above are complete.</p>;
  const eta = clips.etaSeconds != null ? `, the rest in ${aboutMinutes(clips.etaSeconds)}` : "";
  return (
    <p className="notice">
      Evidence clips: {clips.ready} of {clips.total} ready{eta}.{" "}
      {clips.state === "RENDERING" ? "They are being made now" : "They are waiting their turn"}; the results are final
      and this page adds the clips as they arrive.
    </p>
  );
}

/** Keeps the page fresh while evidence clips are still being made (every half minute, keeping what is shown). */
function useMatchData(load: () => Promise<MatchDetail>, loadEvidence: () => Promise<EvidenceEvent[]>, key: string) {
  const [round, setRound] = useState(0);
  const match = useLoad(load, [key, round], round > 0);
  const evidence = useLoad(loadEvidence, [key, round], round > 0);
  const clips = match.data?.clips;
  const waiting = !!clips && clips.pending > 0 && (clips.state === "QUEUED" || clips.state === "RENDERING");
  useEffect(() => {
    if (!waiting) return;
    const t = setTimeout(() => setRound((r) => r + 1), CLIP_REFRESH_MS);
    return () => clearTimeout(t);
  }, [waiting, round]);
  useEffect(() => setRound(0), [key]);
  return { match, evidence };
}

export function MatchPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { match, evidence } = useMatchData(() => api.match(id), () => api.matchEvidence(id), id);
  // Opened by Valve's match id: move to the id the match is stored under, so the address is the one we link to.
  const canonical = match.data?.matchId;
  useEffect(() => {
    if (canonical && canonical !== id) navigate(`/matches/${encodeURIComponent(canonical)}`, { replace: true });
  }, [canonical, id, navigate]);
  if (!match.data) return <Loading error={match.error} />;
  return (
    <MatchView data={match.data} evidence={evidence.data} evidenceError={evidence.error}>
      <ShareMatch matchId={id} />
    </MatchView>
  );
}

/** A match page opened through a share link: read-only, also for visitors without an account. */
export function SharedMatchPage({ token }: { token: string }) {
  const { match, evidence } = useMatchData(() => api.sharedMatch(token), () => api.sharedEvidence(token), token);
  if (!match.data)
    return match.error ? (
      <div className="empty">
        <h2>This link doesn't work anymore</h2>
        <p className="muted">Shared match links work for 48 hours, and the person who shared it can remove it sooner.</p>
      </div>
    ) : (
      <Loading error={null} />
    );
  const share = match.data.share;
  return (
    <MatchView data={match.data} evidence={evidence.data} evidenceError={evidence.error}>
      <p className="notice small">
        Someone shared this match analysis with you. The link works until{" "}
        {share ? new Date(share.expiresAt).toLocaleString() : "it expires"}.
        {share?.matchVisible && (
          <> You were in this match: <Link to={`/matches/${encodeURIComponent(match.data.matchId)}`}>open your own match page</Link>.</>
        )}{" "}
        Player pages open only for players you have played with or against, after <Link to="/">signing in</Link>.
      </p>
    </MatchView>
  );
}

function ShareMatch({ matchId }: { matchId: string }) {
  const [links, setLinks] = useState<ShareLink[] | null>(null);
  const [fresh, setFresh] = useState<ShareLink | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    setFresh(null);
    api.shares(matchId).then(setLinks, () => setLinks([]));
  }, [matchId]);

  async function create() {
    setError(null);
    try {
      const link = await api.createShare(matchId);
      setFresh(link);
      setLinks((l) => [link, ...(l ?? [])]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  async function remove(id: number) {
    await api.deleteShare(id).catch(() => undefined);
    setLinks((l) => (l ?? []).filter((x) => x.id !== id));
    if (fresh?.id === id) setFresh(null);
  }
  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the link is still shown to copy by hand */
    }
  }
  const until = (iso: string) => new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });

  return (
    <details className="share">
      <summary>Share this match</summary>
      <p className="small muted">
        Anyone with the link can see this page, with classes, evidence events and clips, for 48 hours without signing
        in. Player pages are not shared: they still need an account that has played with or against the player.
      </p>
      {fresh?.url ? (
        <div className="share-link">
          <input readOnly value={fresh.url} onFocus={(e) => e.currentTarget.select()} aria-label="Share link" />
          <button className="button small-button" onClick={() => copy(fresh.url!)}>{copied ? "Copied" : "Copy"}</button>
        </div>
      ) : (
        <button className="button small-button" onClick={create}>Create link</button>
      )}
      {error && <p className="error small">{error}</p>}
      {links && links.length > 0 && (
        <ul className="share-list small">
          {links.map((l) => (
            <li key={l.id}>
              Link made {new Date(l.createdAt).toLocaleString()}, works until {until(l.expiresAt)}{" "}
              <button className="button small-button danger" onClick={() => remove(l.id)}>Stop sharing</button>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

function MatchView({ data, evidence, evidenceError, children }: {
  data: MatchDetail;
  evidence: EvidenceEvent[] | null;
  evidenceError: string | null;
  children?: ReactNode;
}) {
  const me = useUser()?.steamId ?? "";
  const names = Object.fromEntries(data.players.map((p) => [p.steamId, p.name ?? p.steamId]));
  const classes = Object.fromEntries(data.players.map((p) => [p.steamId, p.assessment?.classification]));

  // The viewer's team first; with no known team, the team that started CT first (CS2's own order).
  const mine = data.players.find((p) => p.steamId === me)?.team;
  const first: Team = mine === 2 || mine === 3 ? mine : 3;
  const teams: (number | null)[] = [first, other(first)];
  const rest = [...new Set(data.players.map((p) => p.team))].filter((t) => t !== 2 && t !== 3);
  const s = data.score;

  return (
    <>
      <div className="match-hero">
        <MapBanner map={data.map} height={170}>
          <div className="hero-content">
            <div>
              <h1 className="map-title">{mapName(data.map)}</h1>
              <div className="hero-meta">
                {[modeName(data.mode), when(data.playedAt ?? data.processedAt)].filter(Boolean).join(" · ")}
              </div>
            </div>
            {s && (
              <div className="hero-teams">
                {teams.map((t, i) => (
                  <span key={String(t)} style={{ display: "contents" }}>
                    {i === 1 && <span className="hero-sep">:</span>}
                    <span className={`hero-team ${s[String(t) as "2"] > s[String(other(t as Team)) as "2"] ? "won" : "lost"}`}>
                      {i === 0 && <SideEmblem side={t} size={30} />}
                      <span>
                        <span className="num">{s[String(t) as "2"]}</span>
                        <br />
                        <span className="label">{t === mine ? "Your team" : `Started ${SIDE_NAME[t as Team]}`}</span>
                      </span>
                      {i === 1 && <SideEmblem side={t} size={30} />}
                    </span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </MapBanner>
        <Rounds rounds={data.rounds} />
        {data.valveDemo && <ValveDemo demo={data.valveDemo} />}
      </div>

      {children}

      {data.processingStatus !== "COMPLETED" && (
        <p className="notice">
          {data.processingStatus === "FAILED"
            ? data.error ?? "The analysis of this demo failed."
            : matchStatusText(data.processingStatus)}
        </p>
      )}

      {[...teams, ...rest].map((t) =>
        data.players.some((p) => p.team === t) ? (
          <TeamTable key={String(t)} team={t} data={data} me={me} won={s && (t === 2 || t === 3) ? s[String(t) as "2"] : null} />
        ) : null,
      )}
      <p className="muted small">
        Kills, damage and ranks are shown for context only; they are never used as evidence. A single match is weak
        evidence on its own: the player page combines all analyzed matches.
      </p>

      <h2>Evidence events</h2>
      {data.clips && <ClipsNotice clips={data.clips} />}
      {!evidence ? <Loading error={evidenceError} /> : <EvidenceList events={evidence} names={names} classes={classes} />}
    </>
  );
}
