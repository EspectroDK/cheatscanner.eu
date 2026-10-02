import type { ReactNode } from "react";

// A small Markdown renderer for our own docs (docs/how-it-works.md): headings, paragraphs, bullet and numbered
// lists, tables, **bold**, *italic*, `code` and links. It builds React elements, never raw HTML. Links to other
// repository files render as plain text, since the repository isn't part of the site. HTML comments are dropped,
// and anything between <!-- repo-only --> and <!-- /repo-only --> is left out.

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = out.length;
    if (m[2] !== undefined) out.push(<strong key={k}>{inline(m[2])}</strong>);
    else if (m[3] !== undefined) out.push(<em key={k}>{inline(m[3])}</em>);
    else if (m[4] !== undefined) out.push(<code key={k}>{m[4]}</code>);
    else if (/^https?:\/\//.test(m[6])) out.push(<a key={k} href={m[6]} target="_blank" rel="noreferrer">{m[5]}</a>);
    else out.push(m[5]);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (row: string) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export function Markdown({ source }: { source: string }) {
  const text = source
    .replace(/<!-- repo-only -->[\s\S]*?<!-- \/repo-only -->/g, "")
    .replace(/<!--[\s\S]*?-->/g, "");
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const k = blocks.length;
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (!line.trim()) {
      i++;
    } else if (heading) {
      const level = heading[1].length;
      const content = inline(heading[2]);
      blocks.push(level === 1 ? <h1 key={k}>{content}</h1> : level === 2 ? <h2 key={k}>{content}</h2> : <h3 key={k}>{content}</h3>);
      i++;
    } else if (line.startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const [head, , ...body] = rows;
      blocks.push(
        <div key={k} className="doc-table">
          <table>
            <thead><tr>{cells(head).map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
            <tbody>{body.map((r, n) => <tr key={n}>{cells(r).map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
    } else if (/^(- |\d+\. )/.test(line)) {
      const ordered = /^\d+\. /.test(line);
      const items: string[] = [];
      while (i < lines.length && lines[i].trim()) {
        if (/^(- |\d+\. )/.test(lines[i])) items.push(lines[i].replace(/^(- |\d+\. )/, ""));
        else items[items.length - 1] += " " + lines[i].trim();
        i++;
      }
      const lis = items.map((it, j) => <li key={j}>{inline(it)}</li>);
      blocks.push(ordered ? <ol key={k}>{lis}</ol> : <ul key={k}>{lis}</ul>);
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() && !/^(#|\||- |\d+\. )/.test(lines[i])) para.push(lines[i++].trim());
      blocks.push(<p key={k}>{inline(para.join(" "))}</p>);
    }
  }
  return <>{blocks}</>;
}
