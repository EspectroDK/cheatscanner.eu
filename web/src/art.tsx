// Our own artwork: logo, side emblems and map banners. Drawn here as SVG so there are no licensing
// questions around Valve's images. A real map image can still be dropped into web/public/maps/<map>.jpg
// and is shown on top of the drawn banner when present (see web/public/maps/SOURCE.md).
import { useState } from "react";

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="logo">
      <circle cx="14" cy="14" r="9.5" fill="none" stroke="currentColor" strokeWidth="2.6" />
      <path d="M21 21l7 7" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M14 7.5v4M14 16.5v4M7.5 14h4M16.5 14h4" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="14" cy="14" r="1.3" fill="var(--accent)" />
    </svg>
  );
}

/** The brand name as a wordmark: "cheatscanner" with the ".eu" of cheatscanner.eu in the accent colour. */
export function Wordmark() {
  return (
    <span className="wordmark">
      cheatscanner<span className="wordmark-tld">.eu</span>
    </span>
  );
}

// Side emblems: T = amber diamond with a flame-like cut, CT = blue shield with a chevron.
export function SideEmblem({ side, size = 22 }: { side: number | null | undefined; size?: number }) {
  if (side === 2)
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" role="img" aria-label="T" className="emblem">
        <path d="M12 1.5l9.5 10.5L12 22.5 2.5 12z" fill="var(--t)" />
        <path d="M12 6.5c1.8 2.4 3.6 4 3.6 6.4A3.6 3.6 0 0112 16.5a3.6 3.6 0 01-3.6-3.6c0-1.2.6-2.2 1.4-3 .1 1.2.8 2 1.7 2.2-.3-2 .1-4 .5-5.6z"
          fill="#1a1206" />
      </svg>
    );
  if (side === 3)
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" role="img" aria-label="CT" className="emblem">
        <path d="M12 1.5l9 3.2v6.8c0 5.4-3.8 9.4-9 11-5.2-1.6-9-5.6-9-11V4.7z" fill="var(--ct)" />
        <path d="M7 9.5l5 3.5 5-3.5M7 13.5l5 3.5 5-3.5" fill="none" stroke="#08131f" strokeWidth="1.9" strokeLinejoin="round" />
      </svg>
    );
  return null;
}

export const SIDE_NAME: Record<number, string> = { 2: "T", 3: "CT" };

interface MapStyle { name: string; from: string; to: string; motif: "arches" | "dunes" | "towers" | "waves" | "gears" | "blocks" | "peaks" }

const MAPS: Record<string, MapStyle> = {
  de_mirage: { name: "Mirage", from: "#c9894a", to: "#6b3f22", motif: "arches" },
  de_dust2: { name: "Dust II", from: "#d8b36a", to: "#7a5a2c", motif: "dunes" },
  de_inferno: { name: "Inferno", from: "#c4643f", to: "#5c2a1c", motif: "towers" },
  de_nuke: { name: "Nuke", from: "#4f8fb0", to: "#1d3a4d", motif: "blocks" },
  de_ancient: { name: "Ancient", from: "#5f9a6b", to: "#233f2c", motif: "peaks" },
  de_anubis: { name: "Anubis", from: "#3fa3a0", to: "#173f45", motif: "towers" },
  de_vertigo: { name: "Vertigo", from: "#7a8fa8", to: "#2c3746", motif: "blocks" },
  de_overpass: { name: "Overpass", from: "#6f9a8a", to: "#27403a", motif: "waves" },
  de_train: { name: "Train", from: "#8f8676", to: "#3a352c", motif: "gears" },
  de_train_2: { name: "Train", from: "#8f8676", to: "#3a352c", motif: "gears" },
};

export const mapName = (m: string | null | undefined) =>
  m ? (MAPS[m]?.name ?? m.replace(/^de_|^cs_/, "").replace(/(^|_)\w/g, (c) => c.replace("_", " ").toUpperCase())) : "Unknown map";

function Motif({ motif }: { motif: MapStyle["motif"] }) {
  const s = { fill: "none", stroke: "rgba(255,255,255,0.13)", strokeWidth: 3 } as const;
  switch (motif) {
    case "arches":
      return <g {...s}>{[0, 1, 2, 3, 4].map((i) => <path key={i} d={`M${230 + i * 38} 160v-50a19 19 0 0138 0v50`} />)}</g>;
    case "dunes":
      return <g {...s}><path d="M180 150c60-40 120-40 180 0M240 165c50-30 100-30 150 0M150 130c40-25 80-25 120 0" /></g>;
    case "towers":
      return <g {...s}><path d="M260 160V80l20-18 20 18v80M320 160V100l16-14 16 14v60M372 160V70l22-20 22 20v90" /></g>;
    case "blocks":
      return <g {...s}><rect x="250" y="70" width="60" height="90" /><rect x="320" y="95" width="45" height="65" /><rect x="375" y="55" width="40" height="105" /></g>;
    case "peaks":
      return <g {...s}><path d="M200 160l60-80 40 50 40-70 70 100" /></g>;
    case "waves":
      return <g {...s}><path d="M180 120q30-20 60 0t60 0 60 0 60 0M180 145q30-20 60 0t60 0 60 0 60 0" /></g>;
    case "gears":
      return <g {...s}><circle cx="320" cy="115" r="38" /><circle cx="320" cy="115" r="14" /><path d="M200 160h240M200 150h240" /></g>;
  }
}

// A map banner: gradient + motif + name, with an optional real image layered on top.
export function MapBanner({ map, height = 120, children }: { map: string | null | undefined; height?: number; children?: React.ReactNode }) {
  const style = (map && MAPS[map]) || { name: mapName(map), from: "#5b6573", to: "#232a33", motif: "blocks" as const };
  const [img, setImg] = useState(true);
  const gid = `g-${map ?? "none"}`;
  return (
    <div className="map-banner" style={{ height }}>
      <svg viewBox="0 0 440 160" preserveAspectRatio="xMaxYMid slice" aria-hidden="true">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={style.from} />
            <stop offset="1" stopColor={style.to} />
          </linearGradient>
        </defs>
        <rect width="440" height="160" fill={`url(#${gid})`} />
        <Motif motif={style.motif} />
      </svg>
      {map && img && <img src={`maps/${map}.jpg`} alt="" onError={() => setImg(false)} />}
      <div className="map-banner-shade" />
      <div className="map-banner-content">{children ?? <span className="map-title">{style.name}</span>}</div>
    </div>
  );
}
