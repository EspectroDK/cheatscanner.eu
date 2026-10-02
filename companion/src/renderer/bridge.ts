// The app's API as the windows see it. Inside the app it comes from the preload script; in a plain
// browser (`npm run dev:ui`) a simulation stands in, so every screen can be looked at without the app.
// Pick a screen with ?screen=unlinked | linking | lobby | detail | waiting | problem (default: lobby).

import { DEFAULT_HOTKEYS, hotkeyProblem } from "../shared/hotkeys";
import type { AppState, Bridge, LobbyRow, PlayerDetail } from "../shared/types";

declare global {
  interface Window {
    cheatscanner?: Bridge;
  }
}

const ROSTER: [string, "T" | "CT", LobbyRow["classification"], number, LobbyRow["status"]][] = [
  ["Nova", "CT", "NORMAL", 41, "ok"],
  ["Kestrel", "CT", "NORMAL", 18, "ok"],
  ["mintleaf", "CT", "INSUFFICIENT_DATA", 1, "ok"],
  ["Oberon_7", "CT", "ELEVATED", 9, "ok"],
  ["pixelwolf", "CT", "NORMAL", 27, "ok"],
  ["Brakk", "T", "HIGH", 12, "ok"],
  ["n0va", "T", "NORMAL", 6, "ok"],
  ["sundial", "T", null, 0, "loading"],
  ["Tamsin", "T", null, 0, "no-steam-id"],
  ["quietfox", "T", "NORMAL", 33, "ok"],
];

const daysAgo = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString();
const DETAIL: Record<string, PlayerDetail> = {
  Brakk: {
    evidenceScore: 91, highEvidenceMatches: 8,
    axes: { wallTracking: "HIGH", aim: "MEDIUM", reaction: "LOW" },
    recent: [
      { map: "de_mirage", evidenceScore: 94, playedAt: daysAgo(3) },
      { map: "de_ancient", evidenceScore: 89, playedAt: daysAgo(5) },
      { map: "de_inferno", evidenceScore: 92, playedAt: daysAgo(7) },
    ],
  },
  Oberon_7: {
    evidenceScore: 38, highEvidenceMatches: 1,
    axes: { wallTracking: "LOW", aim: "MEDIUM", reaction: "MEDIUM" },
    recent: [{ map: "de_nuke", evidenceScore: 44, playedAt: daysAgo(12) }],
  },
};

function simulated(): Bridge {
  const screen = new URLSearchParams(location.search).get("screen") ?? "lobby";
  const inGame = screen === "lobby" || screen === "detail";
  let state: AppState = {
    version: "0.1.0",
    server: { url: "https://cheatscanner.eu", domain: "cheatscanner.eu", reachable: true, authEnabled: true },
    account: screen === "unlinked" || screen === "linking" ? null
      : { steamId: "76561198000000001", personaName: "Nova", avatarUrl: null },
    pairing: screen === "linking"
      ? { userCode: "K7QF-M2XP", verifyUrl: "https://cheatscanner.eu/#/link?code=K7QF-M2XP", expiresAt: new Date(Date.now() + 14 * 60_000).toISOString() }
      : null,
    notice: null,
    game: {
      source: "steam",
      running: inGame || screen === "waiting",
      problem: screen === "problem"
        ? "Steam isn't running (or isn't signed in). Start Steam and try again."
        : null,
    },
    match: inGame ? { map: "de_mirage", mode: "premier", phase: "warmup" } : null,
    lobby: {
      rows: inGame ? ROSTER.map(([name, side, classification, matchesAnalyzed, status], slot) => ({
        slot, name, side, classification, matchesAnalyzed, status, isLocal: slot === 0, detail: DETAIL[name] ?? null,
        steamId: status === "no-steam-id" ? null : String(76561198000000001n + BigInt(slot)),
      })) : [],
      updatedAt: inGame ? new Date().toISOString() : null,
      error: null,
    },
    overlay: { hotkey: "Shift+F2", detailHotkey: "F7", mode: "window", visible: false, view: screen === "detail" ? "detail" : "lobby", siren: true },
    hotkeysEditable: true,
    startWithWindows: false,
    alert: { seq: 0, names: [] },
  };
  const listeners = new Set<(s: AppState) => void>();
  const set = (patch: Partial<AppState>) => {
    state = { ...state, ...patch };
    listeners.forEach((l) => l(state));
  };
  return {
    getState: async () => state,
    onState: (l) => (listeners.add(l), () => listeners.delete(l)),
    startLink: async () => set({ pairing: { userCode: "K7QF-M2XP", verifyUrl: "#", expiresAt: new Date(Date.now() + 15 * 60_000).toISOString() } }),
    cancelLink: async () => set({ pairing: null }),
    openLinkPage: async () => {},
    unlink: async () => set({ account: null }),
    setHotkey: async (which, hotkey) => {
      const problem = hotkeyProblem(hotkey);
      set(problem ? { notice: problem } : { notice: null, overlay: { ...state.overlay, [which === "lobby" ? "hotkey" : "detailHotkey"]: hotkey } });
      return !problem;
    },
    resetHotkeys: async () => (set({ overlay: { ...state.overlay, hotkey: DEFAULT_HOTKEYS.lobby, detailHotkey: DEFAULT_HOTKEYS.detail } }), true),
    pauseHotkeys: async () => {},
    setStartWithWindows: async (on) => set({ startWithWindows: on }),
    toggleOverlay: async () => set({ overlay: { ...state.overlay, visible: !state.overlay.visible, view: "lobby" } }),
    toggleDetail: async () => set({ overlay: { ...state.overlay, visible: true, view: state.overlay.view === "detail" ? "lobby" : "detail" } }),
    setSiren: async (siren) => set({ overlay: { ...state.overlay, siren } }),
    testSiren: async () => set({ alert: { seq: state.alert.seq + 1, names: [] } }),
    openPlayer: async () => {},
  };
}

export const bridge: Bridge = window.cheatscanner ?? simulated();
