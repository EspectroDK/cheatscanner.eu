import { useState } from "react";
import { Link } from "react-router-dom";
import { Logo, Wordmark } from "../art";
import { api } from "../api";
import { ClassBadge, SOURCE_URL, useLoad } from "../ui";

const STEPS: [string, string][] = [
  ["Sign in through Steam", "Steam only tells us your public Steam ID. We never see your password."],
  [
    "Connect your match history",
    "With your Steam match-history code, new Competitive and Premier matches are fetched from Valve and analyzed automatically. You can also upload a demo yourself.",
  ],
  [
    "See who stood out",
    "Every player in your matches gets an evidence class, with the moments behind it: short reconstructions of the round, drawn from the demo.",
  ],
];

const SIGNALS: [string, string][] = [
  ["Hidden information", "Aim that follows an enemy through walls, or settles on them before they can be seen or heard."],
  ["Aim mechanics", "Turns onto a target that are faster or cleaner than people move a mouse."],
  ["Shot timing", "Shots fired the instant an enemy enters the crosshair, again and again."],
  ["Recoil", "Spray control that cancels recoil more precisely than a hand can."],
];

const CLASSES: [string, string][] = [
  ["NORMAL", "Nothing beyond what legitimate players show. Most players are here, including very good ones."],
  ["ELEVATED", "Some unusual moments that are worth a look. Skill and luck produce these too, so it is not a suspicion on its own."],
  ["HIGH", "Strong unusual behavior that repeats across rounds or matches. Watch the moments behind it before drawing conclusions."],
];

/** The app's Microsoft Store page; on Windows it opens the Store app. */
const STORE_URL = "https://apps.microsoft.com/detail/9N3R274VP3GV";

const n = (x: number) => x.toLocaleString("en-US");

/** Usage so far and what the scoring was calibrated on: counts only, so it is safe to show signed out. */
function UsageStrip() {
  const st = useLoad(api.siteStats, []).data;
  if (!st) return null;
  const c = st.calibration;
  const tiles: [string, number, string][] = [
    ["Matches analyzed", st.matchesAnalyzed, `${n(st.matchesAnalyzed7d)} in the last 7 days`],
    ["Players analyzed", st.playersAnalyzed, "distinct Steam accounts"],
    ["Minutes of game time", st.gameMinutes, `${n(st.roundsAnalyzed)} rounds`],
    ["Reference matches", c.datasetMatches + c.proMatches, "labelled and pro matches"],
  ];
  return (
    <section className="usage" aria-label="Usage so far">
      <div className="usage-tiles">
        {tiles.map(([label, value, sub]) => (
          <div key={label} className="usage-tile">
            <div className="usage-value">{n(value)}</div>
            <div className="usage-label">{label}</div>
            <div className="muted small">{sub}</div>
          </div>
        ))}
      </div>
      <p className="muted small">
        The scoring was calibrated on {n(c.datasetMatches)} matches on {c.datasetMaps} maps from the public CS2CD dataset
        ({n(c.datasetCleanMatches)} without cheaters, {n(c.datasetLabelledCheaters)} players later VAC-banned), {c.proMatches} professional
        matches ({c.proPlayers} players) and {c.matchmakingDemos} matchmaking demos. <Link to="/how-it-works">How well it works</Link>.
      </p>
    </section>
  );
}

/** Steam sign-in. The session cookie outlives the browser only when "Keep me signed in" is ticked. */
function SteamSignIn() {
  const [remember, setRemember] = useState(false);
  return (
    <div className="signin-action">
      <a className="button primary" href={remember ? "/auth/steam/login?remember=1" : "/auth/steam/login"}>
        Sign in through Steam
      </a>
      <label className="check small">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
        Keep me signed in for 30 days on this device
      </label>
    </div>
  );
}

/** The front page. Signed out it offers Steam sign-in; signed in (reached from the logo) it links to your matches. */
export function SignIn({ linkingApp = false, signedIn = false }: { linkingApp?: boolean; signedIn?: boolean }) {
  const Wrapper = signedIn ? "div" : "main";
  return (
    <Wrapper className={signedIn ? "signin" : "page signin"}>
      <div className="logo-big"><Logo size={64} /></div>
      <h1 className="signin-title"><Wordmark /></h1>
      <p className="signin-lede">
        Analyzes the demos of your Counter-Strike 2 matches for behavior that is unusual enough to look at twice, and
        shows the evidence for the players you have played with or against.
      </p>
      {linkingApp && (
        <p className="notice">Sign in with the Steam account you play on to link the Cheatscanner app on your PC.</p>
      )}
      {signedIn ? (
        <Link className="button primary" to="/">Go to my matches</Link>
      ) : (
        <SteamSignIn />
      )}

      <UsageStrip />

      <section className="intro" aria-label="How it works">
        <h2>How it works</h2>
        <ol className="intro-steps">
          {STEPS.map(([title, text]) => (
            <li key={title}>
              <strong>{title}</strong>
              <span className="muted">{text}</span>
            </li>
          ))}
        </ol>

        <h2>What it looks for</h2>
        <div className="intro-signals">
          {SIGNALS.map(([title, text]) => (
            <div key={title}>
              <strong>{title}</strong>
              <span className="muted small">{text}</span>
            </div>
          ))}
        </div>

        <h2>The three evidence classes</h2>
        <div className="intro-classes">
          {CLASSES.map(([c, text]) => (
            <div key={c} className={`intro-class class-${c.toLowerCase()}`}>
              <ClassBadge value={c} size="lg" />
              <span className="muted small">{text}</span>
            </div>
          ))}
        </div>
        <p className="muted small">
          A player with too few analyzed matches is shown as "Not enough data". Classes describe behavior in analyzed
          matches, not a verdict or a probability.
        </p>

        <div className="intro-app">
          <div className="intro-app-head">
            <h2>In your lobby, before you play</h2>
            <span className="soon">In the Microsoft Store</span>
          </div>
          <p>
            The Cheatscanner app for Windows shows the evidence class of every player in your match while it loads, so
            you know who has stood out in earlier analyzed matches before the first round.
          </p>
          <ul className="intro-list">
            <li>Shows only the class (Normal, Elevated, High) and how many analyzed matches it's based on.</li>
            <li>
              A small overlay appears on top of CS2 in warm-up and hides when the match goes live. A hotkey (Shift+F2 by default) shows the
              list again. The game needs to run in Fullscreen Windowed mode.
            </li>
            <li>
              Finds the players in your match through Steam's "recently played with" list and CS2's official Game State
              Integration. It never reads the game's memory or changes the game.
            </li>
            <li>Link the app to this website with the same Steam account.</li>
          </ul>
          <div className="intro-app-download">
            <a href={STORE_URL} target="_blank" rel="noopener noreferrer" className="store-badge">
              {/* Microsoft's own "Get it from Microsoft" badge, linked as their badge guidelines describe. */}
              <img src="https://get.microsoft.com/images/en-us%20dark.svg" alt="Get it from Microsoft" width={200} height={72} />
            </a>
            <span className="muted small">For Windows 10 and 11. Installs and updates through the Microsoft Store.</span>
          </div>
        </div>

        <h2>What it is not</h2>
        <ul className="intro-list">
          <li>
            <strong>Not a verdict.</strong> Good players and lucky rounds produce unusual moments too. One moment is never
            enough; a class rises only when strong signs repeat, and every class links to the moments behind it.
          </li>
          <li>
            <strong>Not inside the game.</strong> Analysis runs on demo files after the match. Cheatscanner never reads
            CS2's memory or changes the game.
          </li>
          <li>
            <strong>Not a public lookup.</strong> On the website you only see yourself and players you have shared a
            match with. The app shows the class of the players in your current match.
          </li>
        </ul>
        <p className="muted small">
          <Link to="/how-it-works">How the analysis works</Link> explains every measurement behind a class. Demos are deleted
          after analysis and chat is never read. See <Link to="/privacy">Privacy</Link> for what is kept. The
          <a href={SOURCE_URL}>source code</a> is open (AGPL-3.0).
        </p>
      </section>
    </Wrapper>
  );
}
