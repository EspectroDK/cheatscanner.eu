import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { mapName } from "../art";
import { EvidenceList } from "../Evidence";
import { Timeline } from "../Timeline";
import { Pattern } from "../Pattern";
import { Why } from "../Why";
import { BanBadge, ClassBadge, Loading, pct, useLoad, useUser, when } from "../ui";

const LEVEL: Record<string, number> = { NORMAL: 0, ELEVATED: 1, HIGH: 2, VERY_HIGH: 2 };

/** A match rated above the player's overall class needs a word, or the two badges read as a contradiction. */
function AboveOverall({ overall, matchClasses }: { overall: string | null | undefined; matchClasses: (string | null | undefined)[] }) {
  if (!overall || !(overall in LEVEL)) return null;
  const above = matchClasses.filter((c) => c && c in LEVEL && LEVEL[c] > LEVEL[overall]).length;
  if (above === 0) return null;
  return (
    <p className="notice small">
      {above === 1 ? "One of their analyzed matches is" : `${above} of their analyzed matches are`} rated above
      their overall class. A single unusual match doesn't change the overall class: it rises only when strong signs
      repeat across matches. <Link to="/how-it-works">How it works</Link> explains this under "Step 6: across matches".
    </p>
  );
}

export function PlayerPage() {
  const { sid = "" } = useParams();
  const player = useLoad(() => api.player(sid), [sid]);
  const matches = useLoad(() => api.playerMatches(sid), [sid]);
  const evidence = useLoad(() => api.playerEvidence(sid), [sid]);
  const timeline = useLoad(() => api.playerTimeline(sid), [sid]);
  const pattern = useLoad(() => api.playerPattern(sid), [sid]);
  const self = useUser()?.steamId === sid;
  if (!player.data)
    return (
      <Loading error={player.error && "You can only see players you have played with or against in an analyzed match."} />
    );

  const p = player.data;
  const a = p.assessment;
  const axes: [string, number | undefined][] = [
    ["Aim mechanics", a?.aimScore],
    ["Hidden information", a?.informationScore],
    ["Shot timing", a?.triggerScore],
    ["Recoil", a?.recoilScore],
    ["Impossible input", a?.mechanicalImpossibilityScore],
  ];
  return (
    <>
      <div className="player-head">
        <div>
          <h1>{p.lastKnownName ?? p.steamId}</h1>
          <p className="muted small">
            Steam ID {p.steamId} ·{" "}
            <a href={`https://steamcommunity.com/profiles/${p.steamId}`} target="_blank" rel="noreferrer">
              Steam profile
            </a>
          </p>
          {p.bans && (
            <p className="muted small ban-note">
              <BanBadge bans={p.bans} size="lg" /> Account-level ban on record at Steam, last one about{" "}
              {p.bans.lastBanOn}. It can come from any game and is not part of the evidence class.
            </p>
          )}
        </div>
        <ClassBadge value={a?.classification} size="lg" />
      </div>

      <div className="stat-grid">
        <div className="stat"><div className="label">Matches analyzed</div><div className="value">{a?.matchesAnalyzed ?? p.matchesAnalyzed}</div></div>
        <div className="stat"><div className="label">Matches with strong evidence</div><div className="value">{a?.highSeverityMatches ?? 0}</div></div>
        <div className="stat"><div className="label">Confidence</div><div className="value">{a?.confidenceLevel?.toLowerCase() ?? "–"}</div></div>
        <div className="stat"><div className="label">Evidence score</div><div className="value">{pct(a?.historicalEvidenceScore)}</div></div>
      </div>

      <Why
        classification={a?.classification}
        eventScore={a?.eventEvidenceScore}
        pattern={a?.profile}
        eventCount={evidence.data ? evidence.data.length : null}
        elevatedThreshold={a?.elevatedThreshold}
      />

      <AboveOverall overall={a?.classification} matchClasses={(timeline.data ?? matches.data ?? []).map((m) => m.classification)} />

      {pattern.data && <Pattern data={pattern.data} />}

      {a && (
        <div className="axis-bars">
          {axes.map(([label, v]) => (
            <div key={label} className="axis-row">
              <span>{label}</span>
              <span className="bar"><span style={{ width: `${Math.round(100 * Math.min(1, Math.max(0, v ?? 0)))}%` }} /></span>
              <span className="num-col muted">{pct(v)}</span>
            </div>
          ))}
        </div>
      )}

      <h2>Evidence over time</h2>
      <p className="muted small">
        Each dot is one analyzed match, oldest on the left, including matches you weren't in. Higher means stronger
        unusual behavior in that match. The small charts split it by type.
      </p>
      {!timeline.data ? <Loading error={timeline.error} /> : <Timeline points={timeline.data} />}

      <h2>{self ? "Your matches" : "Matches you played together"}</h2>
      {!matches.data ? (
        <Loading error={matches.error} />
      ) : (
        <div className="card-table table-wrap"><table>
          <thead>
            <tr><th>Match</th><th>Date</th><th>Evidence</th><th>Score</th></tr>
          </thead>
          <tbody>
            {matches.data.map((m) => (
              <tr key={m.matchId}>
                <td><Link to={`/matches/${encodeURIComponent(m.matchId)}`}>{mapName(m.map)}</Link></td>
                <td>{when(m.playedAt ?? m.processedAt)}</td>
                <td>
                  <ClassBadge value={m.classification} />
                  {(m.profileStrength ?? 0) > 0 && m.evidenceEventCount === 0 && (
                    <span className="muted small" title="Lifted by the overall play pattern, which has no evidence events"> pattern</span>
                  )}
                </td>
                <td>{pct(m.overallEvidenceScore)}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {matches.data?.some((m) => (m.profileStrength ?? 0) > 0 && m.evidenceEventCount === 0 &&
        ["ELEVATED", "HIGH", "VERY_HIGH"].includes(m.classification)) && (
        <p className="muted small">
          Matches marked "pattern" were lifted by the player's overall play pattern in that match: their typical aim
          and timing over every duel was more unusual than almost all clean players. It has no single moments, so there
          are no evidence events. One such match does not change the player's overall class; it takes the pattern
          repeating across several matches. The numbers are under Play pattern above.
        </p>
      )}

      <h2>Strongest evidence events</h2>
      <p className="muted small">From all of this player's analyzed matches, including ones you weren't in.</p>
      {!evidence.data ? (
        <Loading error={evidence.error} />
      ) : (
        <EvidenceList events={evidence.data} showMatch />
      )}
    </>
  );
}
