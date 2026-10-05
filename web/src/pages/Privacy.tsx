import { Link } from "react-router-dom";
import { api } from "../api";
import { useLoad } from "../ui";

// Keep this page in step with what the code really does: parser/demoparser2_backend.py (which demo data is
// read), storage/models.py (what is stored) and retention in default_config.toml (demo deletion).

export function Privacy({ signedIn }: { signedIn: boolean }) {
  const info = useLoad(api.siteInfo, []);
  const contact = info.data?.contactEmail;
  return (
    <article className="privacy">
      <h1>Privacy</h1>
      <p className="lede">
        Cheatscanner keeps only what it needs to follow players' behavior across matches and to show the evidence
        behind it. This page lists what that is.
      </p>

      <h2>Demo files</h2>
      <ul>
        <li>
          A demo is downloaded from Valve's replay servers (or uploaded by you), analyzed, and then{" "}
          <strong>deleted</strong> as soon as the evidence clips of that match are made (the match's results are shown
          before that).
        </li>
        <li>If an analysis fails, the demo may be kept until the problem is fixed and the match is analyzed again. It is deleted after that.</li>
        <li>We don't keep copies of demos, and we don't offer them for download.</li>
      </ul>

      <h2>What we never read or store</h2>
      <ul>
        <li><strong>Chat.</strong> Text chat in the demo is not read, processed or stored.</li>
        <li><strong>Voice.</strong> Voice data in the demo is not read, processed or stored.</li>
        <li><strong>Your Steam password</strong> or Steam session. Signing in through Steam only tells us your Steam ID.</li>
        <li>
          <strong>Anything on your PC or in the game.</strong> Cheatscanner's code never reads or changes CS2's memory. The
          optional Cheatscanner app for Windows gets the players in your match from Steam's "recently played with" list, and
          only sends their Steam IDs to look up their classes. Those lookups are not stored.
        </li>
      </ul>

      <h2>What we store about players in analyzed matches</h2>
      <p>This applies to all ten players in a match, whether or not they use Cheatscanner.</p>
      <ul>
        <li>Steam ID and the in-game name shown in the demo.</li>
        <li>The match itself: map, date, server name, score, rounds, team, and the scoreboard (kills, deaths, assists, damage, headshots, Premier rating).</li>
        <li>
          Behavior measurements from the recording: how aim, shot timing and recoil control moved relative to where enemies were
          and what the player could see. These are numbers derived from positions and view angles, not the recording itself.
        </li>
        <li>
          Evidence events: the round and moment, a short explanation, and for flagged moments a short reconstruction clip and a
          chart. Clips are drawn from positions and view angles on a simplified map. They are not game footage.
        </li>
        <li>An evidence class per match and across matches (Normal, Elevated, High, or not enough data).</li>
      </ul>

      <h2>What we store about you as a user</h2>
      <ul>
        <li>Your Steam ID, and your public Steam name and avatar for display.</li>
        <li>When you signed in. Sign-in sessions and API tokens are stored only as one-way hashes.</li>
        <li>
          Your Steam match-history authentication code, encrypted, and the share codes of your matches. They are used only to find your
          new matches. You can remove the code in Settings at any time, or reset it on Steam.
        </li>
        <li>Which demos you uploaded, so you can open those matches.</li>
        <li>Share links you make: which match, when, and until when the link works. Only a one-way hash of the link is kept.</li>
        <li>
          If you switch on Steam chat messages: that choice, and the messages our Steam bot sent you (which match, what was found, and
          whether it was delivered).
        </li>
      </ul>

      <h2>Who can see what</h2>
      <ul>
        <li>You see your own matches and results.</li>
        <li>
          You see the evidence of players you have played with or against, from all of their analyzed matches. For matches you
          weren't in, you see the map, date and events, but you can't open the match.
        </li>
        <li>Players you have never shared a match with are shown by name only, without their evidence.</li>
        <li>
          In the Cheatscanner app, you see the evidence class and number of analyzed matches of each player in your current
          match, also players you haven't met before. The app never shows their evidence, clips or match history.
        </li>
        <li>
          If you share a match, anyone with the link can see that match page (scoreboard, evidence classes, events and
          clips, as you see them) for 48 hours, without signing in. You can stop sharing sooner on the match page. The link
          never opens player pages or other matches.
        </li>
        <li>There is no public search, and nothing is posted in the game or in chat.</li>
      </ul>

      <h2>Cookies and browser storage</h2>
      <p>
        Cheatscanner uses no analytics, no advertising and no tracking, and loads no third-party scripts or fonts. It
        stores only what signing in needs, so there is no cookie banner:
      </p>
      <ul>
        <li>
          <strong>cs2a_session</strong> (cookie): keeps you signed in. It lasts 30 days, so you stay signed in on this
          device. Signing out removes it.
        </li>
        <li>
          <strong>cs2a_openid_state</strong> (cookie, 10 minutes): checks that the Steam sign-in that comes back is the one
          your browser started. Removed when the sign-in finishes.
        </li>
        <li>
          <strong>cs2a_pending_app_link</strong> (session storage in this tab): keeps the code of the Cheatscanner app
          you are linking while you sign in. Gone when the tab closes.
        </li>
        <li>
          The Cheatscanner app keeps its sign-in token on your PC, encrypted by Windows, so it stays linked to your account.
        </li>
        <li>
          Signing in happens on Steam's own website, which sets Steam's cookies under Valve's privacy policy. Your
          Steam avatar is shown from Steam's image servers.
        </li>
      </ul>

      <h2>Evidence is not a verdict</h2>
      <p>
        An evidence class describes unusual behavior in analyzed matches. It is not a finding that anyone cheats, and not a
        probability. Good players and lucky rounds produce unusual moments too.
      </p>

      <h2>Questions and requests</h2>
      <p>
        {contact ? (
          <>To ask what is stored about you, to dispute an evidence class, or to have your data removed, write to <a href={`mailto:${contact}`}>{contact}</a>.</>
        ) : (
          <>To ask what is stored about you, to dispute an evidence class, or to have your data removed, contact the operator of this site.</>
        )}
      </p>

      <p className="muted small">
        {signedIn ? <Link to="/">Back to your matches</Link> : <Link to="/">Back to sign in</Link>}
      </p>
    </article>
  );
}
