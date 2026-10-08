import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { TimelinePoint } from "./api";
import { mapName } from "./art";
import { ClassBadge } from "./ui";

// A player's evidence per analyzed match, oldest to newest. The vertical scale is the match's evidence
// strength; it is drawn without numbers on purpose, so it isn't read as a probability of cheating.

const TONE: Record<string, string> = {
  NORMAL: "var(--normal)",
  ELEVATED: "var(--elevated)",
  HIGH: "var(--high)",
  VERY_HIGH: "var(--high)",
};
const tone = (c: string | null | undefined) => TONE[c ?? ""] ?? "var(--muted)";
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "?");
const clamp = (x: number | null | undefined) => Math.min(1, Math.max(0, x ?? 0));

const TYPES: [keyof TimelinePoint["axes"], string][] = [
  ["aim", "Aim mechanics"],
  ["hiddenInformation", "Hidden information"],
  ["shotTiming", "Shot timing"],
  ["recoil", "Recoil"],
  ["mechanicalImpossibility", "Impossible input"],
];

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

function Tip({ p, x, y, width }: { p: TimelinePoint; x: number; y: number; width: number }) {
  const left = Math.min(Math.max(x - 110, 0), Math.max(0, width - 220));
  return (
    <div className="tl-tip" style={{ left, top: y }} role="status">
      <div className="tl-tip-head">
        <strong>{mapName(p.map)}</strong>
        <span className="muted">{p.playedAt ? new Date(p.playedAt).toLocaleDateString() : "unknown date"}</span>
      </div>
      <ClassBadge value={p.classification} />
      <div className="small">
        {p.evidenceEventCount} evidence event{p.evidenceEventCount === 1 ? "" : "s"}
      </div>
      {(p.profileStrength ?? 0) > 0 && <div className="small">Unusual overall play pattern</div>}
      <div className="small muted">{p.matchVisible ? "Click to open the match" : "A match you weren't in"}</div>
    </div>
  );
}

export function Timeline({ points }: { points: TimelinePoint[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const navigate = useNavigate();

  if (points.length < 2)
    return <p className="muted small">The timeline appears once this player has two or more analyzed matches.</p>;

  const H = 190, top = 14, bottom = 30, left = 56, right = 16;
  const w = Math.max(width, 280);
  const plotW = w - left - right, plotH = H - top - bottom;
  const x = (i: number) => left + (points.length === 1 ? plotW / 2 : (i * plotW) / (points.length - 1));
  const y = (v: number) => top + (1 - clamp(v)) * plotH;
  const step = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(plotW / 70))));
  const open = (p: TimelinePoint) => p.matchVisible && p.matchId && navigate(`/matches/${encodeURIComponent(p.matchId)}`);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.overallEvidenceScore)}`).join("");
  const band = plotW / Math.max(1, points.length - 1);

  return (
    <div className="timeline">
      <div className="tl-legend small">
        {(["NORMAL", "ELEVATED", "HIGH"] as const).map((c) => (
          <span key={c}><span className="tl-key" style={{ background: tone(c) }} /><ClassLabel c={c} /></span>
        ))}
        <span className="muted">Hollow: a match you weren't in</span>
      </div>
      <div ref={ref} className="tl-plot" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={w} height={H} role="img" aria-label={`Evidence per match over ${points.length} matches`}>
            {[0, 0.5, 1].map((g) => (
              <line key={g} x1={left} x2={w - right} y1={y(g)} y2={y(g)} className="tl-grid" />
            ))}
            <text x={left - 10} y={y(1) + 4} textAnchor="end" className="tl-axis">strong</text>
            <text x={left - 10} y={y(0) + 4} textAnchor="end" className="tl-axis">none</text>
            {points.map((p, i) =>
              i % step === 0 || i === points.length - 1 ? (
                <text key={i} x={x(i)} y={H - 8} textAnchor="middle" className="tl-axis">{day(p.playedAt)}</text>
              ) : null,
            )}
            <path d={line} className="tl-line" />
            {hover != null && <line x1={x(hover)} x2={x(hover)} y1={top} y2={top + plotH} className="tl-cross" />}
            {points.map((p, i) => (
              <g key={i}>
                <circle
                  cx={x(i)} cy={y(p.overallEvidenceScore)} r={hover === i ? 7 : 5.5}
                  fill={p.matchVisible ? tone(p.classification) : "var(--panel)"}
                  stroke={p.matchVisible ? "var(--panel)" : tone(p.classification)}
                  strokeWidth={p.matchVisible ? 2 : 2.5}
                />
                {/* hit target: the whole column, bigger than the dot */}
                <rect
                  x={x(i) - band / 2} y={top} width={band} height={plotH} fill="transparent"
                  style={{ cursor: p.matchVisible ? "pointer" : "default" }}
                  onMouseEnter={() => setHover(i)} onClick={() => open(p)}
                  onTouchStart={() => setHover(i)}
                />
              </g>
            ))}
          </svg>
        )}
        {hover != null && width > 0 && <Tip p={points[hover]} x={x(hover)} y={y(points[hover].overallEvidenceScore) + 14} width={w} />}
      </div>

      <div className="tl-types">
        {TYPES.map(([k, label]) => (
          <Spark key={k} label={label} values={points.map((p) => p.axes?.[k] ?? 0)} hover={hover} onHover={setHover} />
        ))}
      </div>

      <details className="small">
        <summary>Show as a table</summary>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>Map</th><th>Evidence</th><th className="num-col">Events</th></tr></thead>
            <tbody>
              {points.map((p, i) => (
                <tr key={i}>
                  <td>{p.playedAt ? new Date(p.playedAt).toLocaleDateString() : "–"}</td>
                  <td>{mapName(p.map)}{p.matchVisible ? "" : " (you weren't in it)"}</td>
                  <td><ClassBadge value={p.classification} /></td>
                  <td className="num-col">{p.evidenceEventCount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function ClassLabel({ c }: { c: string }) {
  return <>{c === "NORMAL" ? "Normal" : c === "ELEVATED" ? "Elevated" : "High"}</>;
}

function Spark({ label, values, hover, onHover }: { label: string; values: number[]; hover: number | null; onHover: (i: number | null) => void }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const H = 44, pad = 5;
  const w = Math.max(width, 100);
  const x = (i: number) => pad + (i * (w - 2 * pad)) / Math.max(1, values.length - 1);
  const y = (v: number) => pad + (1 - clamp(v)) * (H - 2 * pad);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
  const area = `${d}L${x(values.length - 1)},${H - pad}L${x(0)},${H - pad}Z`;
  const last = values.length - 1;
  return (
    <div className="tl-spark">
      <div className="small">{label}</div>
      <div ref={ref} onMouseLeave={() => onHover(null)}>
        {width > 0 && (
          <svg width={w} height={H} aria-hidden="true"
               onMouseMove={(e) => {
                 const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
                 onHover(Math.round(((e.clientX - r.left - pad) / (w - 2 * pad)) * last));
               }}>
            <line x1={pad} x2={w - pad} y1={H - pad} y2={H - pad} className="tl-grid" />
            <path d={area} className="tl-area" />
            <path d={d} className="tl-spark-line" />
            {hover != null && hover >= 0 && hover <= last && (
              <circle cx={x(hover)} cy={y(values[hover])} r={4} className="tl-spark-dot" />
            )}
            {hover == null && <circle cx={x(last)} cy={y(values[last])} r={3.5} className="tl-spark-dot" />}
          </svg>
        )}
      </div>
    </div>
  );
}
