import { useState } from "react";
import { api, type MatchAccess } from "../api";

const HELP_URL = "https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128";

// Asks for the Steam game authentication code and one recent match share code. With these, the server
// can list the user's new matches and fetch their demos from Valve, without the user uploading anything.
export function MatchAccessForm({ onDone, submitLabel }: { onDone: (a: MatchAccess) => void; submitLabel: string }) {
  const [authCode, setAuthCode] = useState("");
  const [knownCode, setKnownCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onDone(await api.setMatchAccess(authCode.trim(), knownCode.trim()));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="access-form" onSubmit={submit}>
      <ol>
        <li>
          Open <a href={HELP_URL} target="_blank" rel="noreferrer">Steam Support: access to your match history</a>{" "}
          (signed in to Steam).
        </li>
        <li>
          Under <em>Authentication code</em>, create a code if you don't have one and copy it. It looks like{" "}
          <code>AAAA-AAAAA-AAAA</code>.
        </li>
        <li>
          On the same page, copy the share code of your most recent match. It starts with <code>CSGO-</code>.
        </li>
      </ol>
      <label>
        Authentication code
        <input value={authCode} maxLength={32} placeholder="AAAA-AAAAA-AAAA" autoComplete="off"
               onChange={(e) => setAuthCode(e.target.value)} />
      </label>
      <label>
        Most recent match share code
        <input value={knownCode} maxLength={40} placeholder="CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx" autoComplete="off"
               onChange={(e) => setKnownCode(e.target.value)} />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="button primary" disabled={busy || !authCode.trim() || !knownCode.trim()}>
        {busy ? "Checking with Steam…" : submitLabel}
      </button>
      <p className="muted small">
        The authentication code only lets us read your list of matches; it can't change anything on your account.
        We store it encrypted, and you can remove it in Settings or reset it on Steam at any time.{" "}
        <a href="#/privacy">What we store</a>
      </p>
    </form>
  );
}

export function Onboarding({ access, onDone }: { access: MatchAccess; onDone: (a: MatchAccess) => void }) {
  async function signOut() {
    await api.logout();
    window.location.href = "/";
  }

  return (
    <main className="page onboarding">
      <div className="steps" aria-label="Step 2 of 2"><span className="on" /><span className="on" /></div>
      <p className="muted small">Step 2 of 2 · Signed in with Steam</p>
      <h1>One more step: your match history</h1>
      <p>
        Cheatscanner analyzes the demos of your Competitive and Premier matches automatically. For that, Steam needs
        your permission to share your match history with us.
      </p>
      {access.status === "REJECTED" && (
        <p className="error">
          Steam no longer accepts the code you gave us{access.lastError ? `: ${access.lastError}` : ""}. Please enter
          a new one.
        </p>
      )}
      <section className="panel">
        <MatchAccessForm onDone={onDone} submitLabel="Connect match history" />
      </section>
      <button className="button" onClick={signOut}>Sign out</button>
    </main>
  );
}
