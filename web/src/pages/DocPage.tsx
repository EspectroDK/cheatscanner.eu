import { Link } from "react-router-dom";
import { Markdown } from "../markdown";
// These pages render files from the repository, so the site and the docs never drift apart. The Dockerfile
// copies them next to the web sources for the image build.
import howItWorks from "../../../docs/how-it-works.md?raw";
import credits from "../../../CREDITS.md?raw";

const DOCS = { howItWorks, credits };

export function DocPage({ doc, signedIn }: { doc: keyof typeof DOCS; signedIn: boolean }) {
  return (
    <article className="privacy doc">
      <Markdown source={DOCS[doc]} />
      <p className="muted small">
        {signedIn ? <Link to="/">Back to your matches</Link> : <Link to="/">Back to sign in</Link>}
      </p>
    </article>
  );
}
