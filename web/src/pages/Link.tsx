// Linking the Cheatscanner app (companion/) to this account: the app opens this page with the code it
// shows; the user checks that the codes match and confirms (api/companion.py).
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Logo } from "../art";

const PENDING_KEY = "cs2a_pending_app_link";

/** Keeps the code through the Steam sign-in and onboarding, which leave this page. */
export function rememberPendingLink(code: string | null) {
  try {
    if (code) sessionStorage.setItem(PENDING_KEY, code);
    else sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* storage blocked: the user opens the link from the app again */
  }
}

export function pendingLink(): string | null {
  try {
    return sessionStorage.getItem(PENDING_KEY);
  } catch {
    return null;
  }
}

export function LinkApp() {
  const [params] = useSearchParams();
  const code = (params.get("code") ?? "").toUpperCase().slice(0, 16);
  const [state, setState] = useState<"ask" | "busy" | "done" | "error">("ask");
  const [device, setDevice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setState("busy");
    try {
      const r = await api.confirmAppLink(code);
      setDevice(r.deviceName);
      setState("done");
      rememberPendingLink(null);
    } catch (e) {
      setError((e as Error).message);
      setState("error");
    }
  }

  function cancel() {
    rememberPendingLink(null);
  }

  if (!code)
    return (
      <div className="empty">
        <h2>No link code</h2>
        <p className="muted">Open this page with the Link account button in the Cheatscanner app.</p>
      </div>
    );

  return (
    <section className="panel link-app">
      <div className="logo-big"><Logo size={48} /></div>
      {state === "done" ? (
        <>
          <h1>App linked</h1>
          <p>
            The Cheatscanner app on <strong>{device}</strong> is now linked to your account. You can close this tab; the
            app updates by itself.
          </p>
          <p className="muted small">You can remove the link at any time under <Link to="/settings">Settings</Link>.</p>
        </>
      ) : (
        <>
          <h1>Link the Cheatscanner app</h1>
          <p>Check that the Cheatscanner app on your PC shows this code:</p>
          <div className="link-code">{code}</div>
          <p className="muted small">
            Only confirm if you started this in the app yourself. A linked app can see the evidence class of the players
            in your matches, as your account.
          </p>
          {state === "error" && <p className="error">{error}</p>}
          <div className="upload">
            <button className="button primary" disabled={state === "busy"} onClick={confirm}>
              {state === "busy" ? "Linking…" : "Confirm and link"}
            </button>
            <Link className="button" to="/" onClick={cancel}>Cancel</Link>
          </div>
        </>
      )}
    </section>
  );
}
