// The only bridge between the windows and the main process. Pages get these calls and nothing else
// (no Node, no token).

import { contextBridge, ipcRenderer } from "electron";
import type { AppState, Bridge } from "../shared/types";

const bridge: Bridge = {
  getState: () => ipcRenderer.invoke("state:get"),
  onState(listener) {
    const handler = (_e: unknown, s: AppState) => listener(s);
    ipcRenderer.on("state", handler);
    return () => ipcRenderer.removeListener("state", handler);
  },
  startLink: () => ipcRenderer.invoke("link:start"),
  cancelLink: () => ipcRenderer.invoke("link:cancel"),
  openLinkPage: () => ipcRenderer.invoke("link:open"),
  unlink: () => ipcRenderer.invoke("link:remove"),
  setHotkey: (which, hotkey) => ipcRenderer.invoke("hotkey:set", which, hotkey),
  resetHotkeys: () => ipcRenderer.invoke("hotkey:reset"),
  setStartWithWindows: (on) => ipcRenderer.invoke("startup:set", on),
  pauseHotkeys: (paused) => ipcRenderer.invoke("hotkey:pause", paused),
  toggleOverlay: () => ipcRenderer.invoke("overlay:toggle"),
  toggleDetail: () => ipcRenderer.invoke("overlay:detail"),
  setSiren: (on) => ipcRenderer.invoke("siren:set", on),
  testSiren: () => ipcRenderer.invoke("siren:test"),
  openPlayer: (steamId) => ipcRenderer.invoke("player:open", steamId),
};

contextBridge.exposeInMainWorld("cheatscanner", bridge);
