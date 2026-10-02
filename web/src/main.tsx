import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Link, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, type MatchAccess, type User } from "./api";
import { Admin } from "./pages/Admin";
import { LinkApp, pendingLink, rememberPendingLink } from "./pages/Link";
import { MatchPage } from "./pages/Match";
import { MyMatches } from "./pages/MyMatches";
import { Onboarding } from "./pages/Onboarding";
import { PlayerPage } from "./pages/Player";
import { Privacy } from "./pages/Privacy";
import { DocPage } from "./pages/DocPage";
import { Settings } from "./pages/Settings";
import { SignIn } from "./pages/SignIn";
import { Upload } from "./pages/Upload";
import { Logo, Wordmark } from "./art";
import { SOURCE_URL, UserContext } from "./ui";
import "./style.css";

function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [access, setAccess] = useState<MatchAccess | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const location = useLocation();
  const onPrivacy = location.pathname === "/privacy";
  // Privacy, How it works and Credits are readable before signing in and during onboarding.
  const publicPage = onPrivacy ? <Privacy signedIn={false} />
    : location.pathname === "/how-it-works" ? <DocPage doc="howItWorks" signedIn={false} />
    : location.pathname === "/credits" ? <DocPage doc="credits" signedIn={false} />
    : null;
  // The Cheatscanner app sends people to /link?code=...; keep the code through sign-in and onboarding.
  if (location.pathname === "/link") rememberPendingLink(new URLSearchParams(location.search).get("code"));

  useEffect(() => {
    const unreachable = () => setError("The server can't be reached right now.");
    api.me().then(
      (u) => {
        setUser(u);
        api.matchAccess().then(setAccess, unreachable);
      },
      (e) => {
        if (e instanceof ApiError && e.status === 401) setUser(null);
        else unreachable();
      },
    );
  }, []);

  if (error) return <main className="page"><p className="error">{error}</p></main>;
  if (user === null) return publicPage ? <main className="page">{publicPage}</main> : <SignIn linkingApp={!!pendingLink()} />;
  if (user === undefined || access === undefined) return <main className="page"><p className="muted">Loading…</p></main>;
  // Connecting the Steam match history is part of signing up (plan section 4.2).
  if (access.required && access.status !== "ACTIVE")
    return publicPage ? <main className="page">{publicPage}</main> : <Onboarding access={access} onDone={setAccess} />;

  return (
    <UserContext.Provider value={user}>
      <header className="topbar">
        <Link to="/about" className="brand" aria-label="Cheatscanner front page"><Logo /><Wordmark /></Link>
        <nav>
          <NavLink to="/" end>My matches</NavLink>
          <NavLink to="/upload">Upload</NavLink>
          <NavLink to="/settings">Settings</NavLink>
          {user.isAdmin && <NavLink to="/admin">Admin</NavLink>}
        </nav>
        <Link to={`/players/${user.steamId}`} className="me">
          {user.avatarUrl && <img src={user.avatarUrl} alt="" />}
          <span className="me-name">{user.personaName ?? user.steamId}</span>
        </Link>
      </header>
      <main className="page">
        <ResumeAppLink />
        <Routes>
          <Route path="/link" element={<LinkApp />} />
          <Route path="/about" element={<SignIn signedIn />} />
          <Route path="/" element={<MyMatches />} />
          <Route path="/matches/:id" element={<MatchPage />} />
          <Route path="/players/:sid" element={<PlayerPage />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/settings" element={<Settings />} />
          {user.isAdmin && <Route path="/admin" element={<Admin />} />}
          <Route path="/privacy" element={<Privacy signedIn />} />
          <Route path="/how-it-works" element={<DocPage doc="howItWorks" signedIn />} />
          <Route path="/credits" element={<DocPage doc="credits" signedIn />} />
          <Route path="*" element={<div className="empty"><h2>Page not found</h2><p className="muted"><Link to="/">Back to your matches</Link></p></div>} />
        </Routes>
      </main>
      <footer className="footer">
        Evidence classes describe unusual behavior in analyzed matches. They are not a verdict and not a
        probability that anyone cheats. · <Link to="/how-it-works">How it works</Link> · <Link to="/privacy">Privacy</Link> · <Link to="/credits">Credits</Link> · <a href={SOURCE_URL}>Source code</a> · cheatscanner.eu
      </footer>
    </UserContext.Provider>
  );
}

/** After signing in (or finishing onboarding), go back to linking the app if that's where the user came from. */
function ResumeAppLink() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  useEffect(() => {
    const code = pendingLink();
    if (code && pathname !== "/link") navigate(`/link?code=${encodeURIComponent(code)}`, { replace: true });
  }, [navigate, pathname]);
  return null;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </StrictMode>,
);
