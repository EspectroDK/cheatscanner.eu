import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiToken } from "../api";
import { JOB_LABELS, JOB_RUNNING as RUNNING, Loading, useLoad, when, useUser } from "../ui";
import { MatchAccessForm } from "./Onboarding";

function MatchHistory() {
  const [refresh, setRefresh] = useState(0);
  const access = useLoad(api.matchAccess, [refresh]);
  const [poll, setPoll] = useState(0);
  const jobs = useLoad(api.shareCodes, [refresh, poll], true);
  const [replacing, setReplacing] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  // Live progress: while a match is being fetched or analyzed, refresh the list every few seconds.
  const busy = jobs.data?.some((j) => RUNNING.includes(j.status));
  useEffect(() => {
    if (!busy) return;
    const t = setTimeout(() => setPoll((n) => n + 1), 4000);
    return () => clearTimeout(t);
  }, [busy, jobs.data]);

  async function remove() {
    if (!confirm("Stop fetching your new matches? Matches already analyzed stay.")) return;
    await api.removeMatchAccess();
    window.location.reload(); // onboarding may be required again
  }

  async function retry(code: string) {
    setRetryError(null);
    try {
      await api.retryShareCode(code);
    } catch (e) {
      setRetryError((e as Error).message);
    }
    setPoll((n) => n + 1);
  }

  if (!access.data) return <Loading error={access.error} />;
  const a = access.data;
  return (
    <>
      {a.status === "ACTIVE" ? (
        <p>
          Connected. New matches are picked up automatically
          {a.lastCheckedAt ? `; last checked ${when(a.lastCheckedAt)}` : ""}.
          {a.lastError && <span className="muted small"> Last check had a problem: {a.lastError}</span>}
        </p>
      ) : a.status === "REJECTED" ? (
        <p className="error">Steam no longer accepts your code{a.lastError ? `: ${a.lastError}` : ""}.</p>
      ) : (
        <p>Not connected. Your matches are only analyzed when you upload the demos yourself.</p>
      )}
      {replacing || a.status !== "ACTIVE" ? (
        <MatchAccessForm submitLabel="Save" onDone={() => { setReplacing(false); setRefresh((n) => n + 1); }} />
      ) : (
        <div className="upload">
          <button className="button" onClick={() => setReplacing(true)}>Enter a new code</button>
          <button className="button danger" onClick={remove}>Disconnect</button>
        </div>
      )}
      {retryError && <p className="error">{retryError}</p>}
      {jobs.data && jobs.data.length > 0 && (
        <table>
          <tbody>
            {jobs.data.map((j) => (
              <tr key={j.shareCode}>
                <td className="small"><code>{j.shareCode}</code></td>
                <td className="muted small">{when(j.createdAt)}</td>
                <td>
                  {j.status === "DONE" && j.matchId ? (
                    <Link to={`/matches/${encodeURIComponent(j.matchId)}`}>{JOB_LABELS.DONE}</Link>
                  ) : RUNNING.includes(j.status) ? (
                    <span className="status-pill"><span className="pulse" />{JOB_LABELS[j.status]}</span>
                  ) : (
                    JOB_LABELS[j.status] ?? j.status
                  )}
                  {j.error && j.status !== "DONE" && <div className="muted small">{j.error}</div>}
                </td>
                <td>
                  {j.status === "FAILED" && <button className="button" onClick={() => retry(j.shareCode)}>Retry</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function SteamChatSetting() {
  const [refresh, setRefresh] = useState(0);
  const chat = useLoad(api.steamChat, [refresh]);
  const [error, setError] = useState<string | null>(null);

  async function toggle(enabled: boolean) {
    setError(null);
    try {
      await api.setSteamChat(enabled);
    } catch (e) {
      setError((e as Error).message);
    }
    setRefresh((n) => n + 1);
  }

  if (!chat.data) return <Loading error={chat.error} />;
  const c = chat.data;
  const last = c.lastMessage;
  return (
    <>
      <label className="check">
        <input type="checkbox" checked={c.enabled} onChange={(e) => toggle(e.target.checked)} />
        Send me a Steam chat message with the link when a match of mine has been analyzed
      </label>
      <p className="muted small">
        The message says how many evidence events were found and how many players got a raised evidence class.
      </p>
      {c.enabled && (
        c.friends ? (
          <p>You are friends with the bot, so messages will arrive in Steam chat.</p>
        ) : c.bot ? (
          <p>
            Steam only lets the bot message its friends. <a href={c.bot.profileUrl} target="_blank" rel="noreferrer">Open the
            bot's Steam profile</a> and click Add Friend; it accepts within a minute.
          </p>
        ) : (
          <p className="muted">The bot isn't online right now. Check back here in a few minutes to find it and add it as a friend.</p>
        )
      )}
      {last && (
        <p className="muted small">
          Last message: {last.status === "SENT" ? "sent" : last.status === "SKIPPED" ? "not sent, you weren't friends with the bot"
            : last.status === "FAILED" ? `failed${last.error ? ` (${last.error})` : ""}` : "waiting to be sent"}
          {last.updatedAt ? `, ${when(last.updatedAt)}` : ""}.
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </>
  );
}

export function Settings() {
  const me = useUser()!;
  const [refresh, setRefresh] = useState(0);
  const tokens = useLoad(api.tokens, [refresh]);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<(ApiToken & { token: string }) | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setError(null);
    try {
      setCreated(await api.createToken(name.trim()));
      setName("");
      setRefresh((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function revoke(id: number) {
    await api.revokeToken(id);
    if (created?.id === id) setCreated(null);
    setRefresh((n) => n + 1);
  }

  async function signOut() {
    await api.logout();
    window.location.href = "/";
  }

  return (
    <>
      <h1>Settings</h1>
      <section className="panel">
        <h2>Steam account</h2>
        <p>
          Signed in as <strong>{me.personaName ?? me.steamId}</strong> (Steam ID {me.steamId}).
        </p>
        <button className="button" onClick={signOut}>Sign out</button>
      </section>

      <section className="panel">
        <h2>Steam match history</h2>
        <MatchHistory />
      </section>

      <section className="panel">
        <h2>Steam chat messages</h2>
        <SteamChatSetting />
      </section>

      <section className="panel">
        <h2>Linked apps and API tokens</h2>
        <p className="muted small">
          The Cheatscanner app appears here once you link it (from the app's Link account button). Tokens let a
          program act for your account; anyone who has one can use it, so revoke any you no longer use.
        </p>
        <div className="upload">
          <input value={name} maxLength={64} placeholder="Token name, e.g. companion" onChange={(e) => setName(e.target.value)} />
          <button className="button primary" disabled={!name.trim()} onClick={create}>Create token</button>
        </div>
        {error && <p className="error">{error}</p>}
        {created && (
          <p className="notice">
            Copy this token now; it won't be shown again: <code>{created.token}</code>
          </p>
        )}
        {!tokens.data ? (
          <Loading error={tokens.error} />
        ) : (
          <table>
            <tbody>
              {tokens.data.map((t) => (
                <tr key={t.id}>
                  <td>{t.name}</td>
                  <td className="muted small">created {when(t.createdAt)}</td>
                  <td className="muted small">{t.lastUsedAt ? `last used ${when(t.lastUsedAt)}` : "never used"}</td>
                  <td><button className="button danger" onClick={() => revoke(t.id)}>Revoke</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
