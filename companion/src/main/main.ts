// Electron entry point: windows, IPC, the in-game overlay and the game data source.
//
//   electron .                     live data from Steam's players list + CS2's game-state feed (default)
//   electron . --replay            plays fixtures/premier-mirage.jsonl instead (no CS2 needed)
//   electron . --replay=<file>     plays a recording, e.g. one made with --record
//   ow-electron . --overwolf       Overwolf's CS2 game events and overlay (needs Overwolf's approval)
//   ... --record                   with --overwolf: saves Overwolf's CS2 data to <userData>/recordings
//   ... --minimized                start minimized to the taskbar (used when starting with Windows)
//   ... --server=<url>             talk to another server (default: http://localhost:8000 in
//                                  development, https://cheatscanner.eu when packaged; also
//                                  CHEATSCANNER_SERVER). Not a user setting.
//
// Hard rule: nothing here reads or changes CS2's memory, injects into it or hooks its drawing. The
// default overlay is an ordinary see-through, click-through window kept on top of the game, which works
// when CS2 runs in "Fullscreen Windowed" mode.

import { app, BrowserWindow, globalShortcut, ipcMain, safeStorage, screen, shell, utilityProcess } from "electron";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { hostname } from "node:os";
import { join } from "node:path";
import { DEFAULT_HOTKEYS, type Hotkeys } from "../shared/hotkeys";
import type { AppState } from "../shared/types";
import { Controller, type Settings, type SettingsStore } from "./controller";
import { OverwolfSource, type OverwolfPackages } from "./game/overwolf";
import { record } from "./game/recorder";
import { ReplaySource } from "./game/replay";
import { CS2_GAME_ID, type GameSource } from "./game/source";
import { SteamSource } from "./game/steam";
import type { CoplayResult } from "./steam/coplay";
import { installGsiConfig, newGsiToken, startGsiServer } from "./steam/gsi";

const PUBLIC_SERVER = "https://cheatscanner.eu";
const DEV_SERVER = "http://localhost:8000";
// Overwolf's overlay keeps the default hotkeys (neither is bound in CS2 by default); the app's own overlay
// window uses the ones chosen in Settings.
const HOTKEY = { label: DEFAULT_HOTKEYS.lobby, code: "F2", accelerator: DEFAULT_HOTKEYS.lobby };
const DETAIL_HOTKEY = { label: DEFAULT_HOTKEYS.detail, code: "F7", accelerator: DEFAULT_HOTKEYS.detail };

const root = join(__dirname, "..", "..");
const rendererDir = join(root, "dist", "renderer");
const preload = join(__dirname, "preload.js");

function arg(name: string): string | true | undefined {
  for (const a of process.argv) {
    if (a === `--${name}`) return true;
    if (a.startsWith(`--${name}=`)) return a.slice(name.length + 3);
  }
  return undefined;
}

/** settings.json in the app's data folder; the token is encrypted with Windows' DPAPI when available. */
function settingsStore(): SettingsStore {
  const file = join(app.getPath("userData"), "settings.json");
  return {
    load(): Settings {
      if (!existsSync(file)) return {};
      try {
        const raw = JSON.parse(readFileSync(file, "utf8"));
        let token: string | null = raw.token ?? null;
        if (raw.tokenEnc && safeStorage.isEncryptionAvailable())
          token = safeStorage.decryptString(Buffer.from(raw.tokenEnc, "base64"));
        return { serverUrl: raw.serverUrl, account: raw.account ?? null, token, siren: raw.siren, hotkeys: raw.hotkeys };
      } catch {
        return {};
      }
    },
    save(s: Settings) {
      const out: Record<string, unknown> = { serverUrl: s.serverUrl, account: s.account ?? null, siren: s.siren, hotkeys: s.hotkeys };
      if (s.token && safeStorage.isEncryptionAvailable()) out.tokenEnc = safeStorage.encryptString(s.token).toString("base64");
      else if (s.token) out.token = s.token;
      writeFileSync(file, JSON.stringify(out, null, 2));
    },
  };
}

function overwolfPackages(): OverwolfPackages | undefined {
  return (app as unknown as { overwolf?: { packages?: OverwolfPackages } }).overwolf?.packages;
}

const useOverwolf = !!arg("overwolf") && !arg("replay");

function gameSource(): GameSource {
  const replay = arg("replay");
  if (replay) {
    const file = typeof replay === "string" ? replay : join(root, "fixtures", "premier-mirage.jsonl");
    return ReplaySource.fromFile(file, { loopPauseMs: 15_000 });
  }
  const packages = overwolfPackages();
  if (useOverwolf && packages) return new OverwolfSource(packages);
  return steamSource();
}

/** A random token in <userData>/gsi.json, so only CS2 (reading our cfg file) can post game state. */
function gsiToken(): string {
  const file = join(app.getPath("userData"), "gsi.json");
  try {
    const t = JSON.parse(readFileSync(file, "utf8")).token;
    if (typeof t === "string" && t.length >= 16) return t;
  } catch {
    /* first start */
  }
  const token = newGsiToken();
  writeFileSync(file, JSON.stringify({ token }));
  return token;
}

function steamSource(): SteamSource {
  const token = gsiToken();
  let gsiProblem: string | null = null;
  try {
    const r = installGsiConfig(token);
    if (r.status === "not-found")
      gsiProblem = "Couldn't find CS2's folder, so the app can't tell when a match starts. It still reads Steam's players list.";
    else if (r.status === "installed")
      gsiProblem = "If CS2 is already running, restart it once so it loads Cheatscanner's game-state file.";
  } catch (e) {
    gsiProblem = `Couldn't write CS2's game-state file: ${e instanceof Error ? e.message : e}`;
  }
  return new SteamSource({
    scan: scanCoplay,
    listen: (onState) => {
      const server = startGsiServer(token, onState);
      return () => server.close();
    },
    gsiProblem,
    localSteamId: () => controller?.state.account?.steamId ?? null,
  });
}

/** Reads Steam's players list in a short-lived utility process (steam/coplay-worker.ts). */
function scanCoplay(localSteamId: string | null): Promise<CoplayResult> {
  return new Promise((resolve, reject) => {
    const child = utilityProcess.fork(join(__dirname, "steam", "coplay-worker.js"), [], { serviceName: "Steam players list" });
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("Steam didn't answer. Is Steam running and signed in?"));
    }, 10_000);
    child.once("message", (m: { ok: boolean; result?: CoplayResult; error?: string }) => {
      clearTimeout(timer);
      child.kill();
      if (m.ok && m.result) resolve(m.result);
      else reject(new Error(m.error ?? "Couldn't read Steam's players list."));
    });
    child.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`Reading Steam's players list failed (code ${code}).`));
    });
    child.postMessage({ localSteamId });
  });
}

let desktop: BrowserWindow | null = null;
let overlayWin: BrowserWindow | null = null;
let controller: Controller;

function webPreferences() {
  return { preload, contextIsolation: true, sandbox: true, nodeIntegration: false };
}

function load(win: BrowserWindow, page: "index" | "overlay") {
  const dev = arg("dev-ui");
  if (typeof dev === "string") void win.loadURL(`${dev.replace(/\/$/, "")}/${page}.html`);
  else void win.loadFile(join(rendererDir, `${page}.html`));
}

function createDesktopWindow() {
  desktop = new BrowserWindow({
    width: 460, height: 720, minWidth: 380, minHeight: 520,
    title: "Cheatscanner", backgroundColor: "#0d1014", show: false,
    icon: join(root, "assets", "icon.png"),
    autoHideMenuBar: true,
    webPreferences: webPreferences(),
  });
  // Started with Windows: sit in the taskbar instead of opening over whatever the user is doing.
  desktop.once("ready-to-show", () => (arg("minimized") ? desktop?.minimize() : desktop?.show()));
  desktop.on("closed", () => {
    desktop = null;
    app.quit(); // the overlay has no use without the app
  });
  lockNavigation(desktop);
  load(desktop, "index");
}

/** Pages stay inside the app; links open in the user's browser. */
function lockNavigation(win: BrowserWindow) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e) => e.preventDefault());
}

// ------------------------------------------------------------------ overlay

const OVERLAY_SIZE = { width: 380, height: 640 };

function setupOverlay(): AppState["overlay"] {
  const base = { hotkey: HOTKEY.label, detailHotkey: DETAIL_HOTKEY.label, visible: false, view: "lobby" as const, siren: true };
  const packages = overwolfPackages() as any;
  if (useOverwolf && packages) {
    // The overlay package arrives with 'ready'; until then (or without approval) there is no in-game overlay.
    if (packages.overlay) startOverwolfOverlay(packages.overlay);
    else packages.on("ready", (_e: unknown, name: string) => {
      if (name === "overlay") startOverwolfOverlay(packages.overlay);
    });
    return { ...base, mode: "overwolf" };
  }
  // Our own see-through window on top of the game, with global hotkeys (registered by the controller).
  app.whenReady().then(() => createOverlayWindow());
  return { ...base, mode: "window" };
}

/** Registers the overlay window's hotkeys system-wide; returns why not when one is taken. */
function applyHotkeys(h: Hotkeys): string | null {
  globalShortcut.unregisterAll();
  const ok = [
    globalShortcut.register(h.lobby, () => controller?.toggleOverlay()),
    globalShortcut.register(h.detail, () => controller?.toggleDetail()),
  ];
  const taken = [h.lobby, h.detail].filter((_, i) => !ok[i]);
  if (taken.length === 0) return null;
  globalShortcut.unregisterAll();
  return `${taken.join(" and ")} ${taken.length > 1 ? "are" : "is"} already used by another program. Pick another key.`;
}

function startOverwolfOverlay(overlayApi: any) {
  overlayApi.registerGames({ gamesIds: [CS2_GAME_ID] });
  overlayApi.on("game-launched", (event: { inject: () => void }, gameInfo: { classId?: number }) => {
    if (gameInfo?.classId === CS2_GAME_ID) event.inject();
  });
  overlayApi.on("game-injected", async () => {
    if (overlayWin && !overlayWin.isDestroyed()) return;
    const ow = await overlayApi.createWindow({
      name: "lobby",
      ...OVERLAY_SIZE,
      x: 24, y: 120,
      show: false,
      transparent: true,
      frame: false,
      resizable: false,
      // Display only: mouse and keyboard keep going to the game.
      passthrough: "passThrough",
      zOrder: "topMost",
      dpiAware: true,
      webPreferences: webPreferences(),
    });
    overlayWin = ow.window;
    overlayWin!.on("closed", () => (overlayWin = null));
    load(overlayWin!, "overlay");
    syncOverlay();
  });
  overlayApi.on("game-exit", () => {
    overlayWin?.destroy();
    overlayWin = null;
    controller?.setOverlayVisible(false);
  });
  for (const [name, hk, fn] of [["toggle-lobby", HOTKEY, () => controller?.toggleOverlay()], ["toggle-detail", DETAIL_HOTKEY, () => controller?.toggleDetail()]] as const)
    overlayApi.hotkeys.register(
      { name, keyCode: hk.code, modifiers: { shift: hk.accelerator.startsWith("Shift+") }, passthrough: false },
      (_hk: unknown, state: string) => {
        if (state === "pressed") fn();
      },
    );
}

/** Created hidden at start-up, so the siren can play before the overlay is shown. */
function createOverlayWindow(): BrowserWindow {
  if (overlayWin && !overlayWin.isDestroyed()) return overlayWin;
  const area = screen.getPrimaryDisplay().workArea;
  overlayWin = new BrowserWindow({
    ...OVERLAY_SIZE, x: area.x + 24, y: area.y + 120,
    frame: false, transparent: true, resizable: false, movable: false, alwaysOnTop: true, skipTaskbar: true,
    focusable: false, hasShadow: false, show: false,
    webPreferences: { ...webPreferences(), backgroundThrottling: false, autoplayPolicy: "no-user-gesture-required" },
  });
  // Above a fullscreen-windowed game, and every click goes through to the game.
  overlayWin.setAlwaysOnTop(true, "screen-saver");
  overlayWin.setIgnoreMouseEvents(true);
  overlayWin.on("closed", () => (overlayWin = null));
  lockNavigation(overlayWin);
  load(overlayWin, "overlay");
  return overlayWin;
}

/** Shows or hides the overlay window to match the app state (hotkeys, warm-up, match going live). */
function syncOverlay() {
  if (!controller) return;
  const win = overlayWin && !overlayWin.isDestroyed() ? overlayWin : null;
  if (!win) return;
  const want = controller.state.overlay.visible;
  if (want && !win.isVisible()) {
    win.showInactive();
    win.setAlwaysOnTop(true, "screen-saver");
  } else if (!want && win.isVisible()) win.hide();
}

// ------------------------------------------------------------------ app

/** Starting with Windows, minimized: a login item for the installed app (Windows matches it by path and args). */
const STARTUP_ARGS = ["--minimized"];
const startup = {
  get: () => app.getLoginItemSettings({ args: STARTUP_ARGS }).openAtLogin,
  set: (on: boolean) => app.setLoginItemSettings({ openAtLogin: on, args: STARTUP_ARGS }),
};

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (desktop) {
      if (desktop.isMinimized()) desktop.restore();
      desktop.focus();
    }
  });

  const overlay = setupOverlay();
  const server: string = typeof arg("server") === "string" ? (arg("server") as string)
    : process.env.CHEATSCANNER_SERVER || (app.isPackaged ? PUBLIC_SERVER : DEV_SERVER);

  app.whenReady().then(() => {
    const source = gameSource();
    if (arg("record")) console.log("recording to", record(source, join(app.getPath("userData"), "recordings")));
    controller = new Controller({
      version: app.getVersion(),
      source,
      store: settingsStore(),
      serverUrl: server,
      deviceName: hostname().slice(0, 48) || (process.platform === "linux" ? "Linux PC" : "Windows PC"),
      openExternal: (url) => {
        if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
      },
      overlay,
      applyHotkeys: overlay.mode === "window" ? applyHotkeys : undefined,
      // Store (MSIX) installs can't add a plain login item, so they don't offer it.
      startup: app.isPackaged && process.platform === "win32" && !process.windowsStore ? startup : undefined,
    });
    controller.on("state", (s) => {
      for (const w of [desktop, overlayWin]) if (w && !w.isDestroyed()) w.webContents.send("state", s);
      syncOverlay();
    });

    ipcMain.handle("state:get", () => controller.state);
    ipcMain.handle("link:start", () => controller.startLink());
    ipcMain.handle("link:cancel", () => controller.cancelLink());
    ipcMain.handle("link:open", () => controller.openLinkPage());
    ipcMain.handle("link:remove", () => controller.unlink());
    ipcMain.handle("hotkey:set", (_e, which: unknown, hotkey: unknown) =>
      (which === "lobby" || which === "detail") && typeof hotkey === "string" ? controller.setHotkey(which, hotkey) : false);
    ipcMain.handle("hotkey:reset", () => controller.resetHotkeys());
    ipcMain.handle("startup:set", (_e, on: unknown) => controller.setStartWithWindows(on === true));
    ipcMain.handle("hotkey:pause", (_e, paused: unknown) => {
      if (overlay.mode !== "window") return;
      if (paused === true) globalShortcut.unregisterAll();
      else applyHotkeys({ lobby: controller.state.overlay.hotkey, detail: controller.state.overlay.detailHotkey });
    });
    ipcMain.handle("overlay:toggle", () => controller.toggleOverlay());
    ipcMain.handle("overlay:detail", () => controller.toggleDetail());
    ipcMain.handle("siren:set", (_e, on: unknown) => controller.setSiren(on === true));
    ipcMain.handle("siren:test", () => controller.testSiren());
    ipcMain.handle("player:open", (_e, steamId: unknown) => {
      const url = controller.playerUrl(String(steamId ?? ""));
      if (url) void shell.openExternal(url);
    });

    createDesktopWindow();
    controller.start();
  });

  app.on("will-quit", () => {
    globalShortcut.unregisterAll();
    controller?.stop();
  });
  app.on("window-all-closed", () => app.quit());
}
