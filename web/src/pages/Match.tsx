import { Link, useParams } from "react-router-dom";
import { api, type MatchDetail } from "../api";
import { MapBanner, mapName, SIDE_NAME, SideEmblem } from "../art";
import { EvidenceList } from "../Evidence";
import { ClassBadge, Loading, pct, Rank, useLoad, useUser, when } from "../ui";

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
              <th>Premier</th>
              <th className="num-col">K</th>
              <th className="num-col">D</th>
              <th className="num-col">A</th>
              <th className="num-col">ADR</th>
              <th className="num-col">HS%</th>
              <th>Evidence</th>
              <th className="num-col">Events</th>
            </tr>
          </thead>
          <tbody>
            {players.map((p) => (
              <tr key={p.steamId}>
                <td>
                  <span className="player-cell">
                    {p.visible ? (
                      <Link to={`/players/${p.steamId}`}>{p.name ?? p.steamId}</Link>
                    ) : (
                      <span className="name">{p.name ?? p.steamId}</span>
                    )}
                    {p.steamId === me && <span className="you">YOU</span>}
                  </span>
                </td>
                <td><Rank type={p.rankType} value={p.rankNew} /></td>
                <td className="num-col">{p.kills}</td>
                <td className="num-col">{p.deaths}</td>
                <td className="num-col">{p.assists}</td>
                <td className="num-col">{Math.round(p.damage / rounds)}</td>
                <td className="num-col">{p.kills ? `${Math.round((100 * p.headshots) / p.kills)}%` : "–"}</td>
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
                    <td className="muted small" title="Only shown for players you have played with or against">Hidden</td>
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

export function MatchPage() {
  const { id = "" } = useParams();
  const me = useUser()?.steamId ?? "";
  const { data, error } = useLoad(() => api.match(id), [id]);
  const evidence = useLoad(() => api.matchEvidence(id), [id]);
  if (!data) return <Loading error={error} />;
  const names = Object.fromEntries(data.players.map((p) => [p.steamId, p.name ?? p.steamId]));

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
                {[data.mode, when(data.playedAt ?? data.processedAt)].filter(Boolean).join(" · ")}
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

      {data.processingStatus !== "COMPLETED" && (
        <p className="notice">This match is {data.processingStatus.toLowerCase()}.{data.error && ` ${data.error}`}</p>
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
      {!evidence.data ? <Loading error={evidence.error} /> : <EvidenceList events={evidence.data} names={names} />}
    </>
  );
}
