// Overlay hotkeys, written as Electron accelerators ("Shift+F2", "Ctrl+Alt+K"). Used by the Settings page
// (to turn a key press into a hotkey) and by the main process (to check one before registering it).

export const DEFAULT_HOTKEYS = { lobby: "Shift+F2", detail: "F7" } as const;

export type HotkeyName = keyof typeof DEFAULT_HOTKEYS;
export type Hotkeys = Record<HotkeyName, string>;

const MODIFIERS = ["Ctrl", "Alt", "Shift"] as const;
/** Keys that don't type anything, so they may be used alone or with Shift. */
const QUIET_KEYS = new Set(["Insert", "Delete", "Home", "End", "PageUp", "PageDown"]);
const isFKey = (k: string) => /^F([1-9]|1\d|2[0-4])$/.test(k);
const isTypingKey = (k: string) => /^[A-Z0-9]$/.test(k) || /^num[0-9]$/.test(k);

/** The key part of a KeyboardEvent.code, as an accelerator key name; null for keys we don't offer. */
export function keyFromCode(code: string): string | null {
  if (isFKey(code)) return code;
  let m = /^Key([A-Z])$/.exec(code);
  if (m) return m[1];
  m = /^(?:Digit|Numpad)([0-9])$/.exec(code);
  if (m) return code.startsWith("Numpad") ? `num${m[1]}` : m[1];
  return QUIET_KEYS.has(code) ? code : null;
}

/** A key press as an accelerator, or null while only modifiers are held or the key isn't offered. */
export function hotkeyFromEvent(e: { code: string; ctrlKey: boolean; altKey: boolean; shiftKey: boolean }): string | null {
  const key = keyFromCode(e.code);
  if (!key) return null;
  const mods = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift"].filter(Boolean);
  return [...mods, key].join("+");
}

/** Why a hotkey can't be used, in plain words; null when it's fine. */
export function hotkeyProblem(hotkey: string): string | null {
  const parts = hotkey.split("+");
  const key = parts.pop() ?? "";
  if (parts.some((p) => !(MODIFIERS as readonly string[]).includes(p)) || new Set(parts).size !== parts.length)
    return `${hotkey} isn't a key combination the app can use.`;
  if (!isFKey(key) && !QUIET_KEYS.has(key) && !isTypingKey(key)) return `${hotkey} isn't a key the app can use.`;
  // A hotkey is taken from every program while the app runs, so a typing key needs Ctrl or Alt.
  if (isTypingKey(key) && !parts.includes("Ctrl") && !parts.includes("Alt"))
    return "Letters and digits need Ctrl or Alt, or you couldn't type them anywhere while the app runs.";
  return null;
}

/** Stored hotkeys, falling back to the defaults for anything missing or unusable. */
export function cleanHotkeys(h: Partial<Hotkeys> | undefined): Hotkeys {
  const lobby = h?.lobby && !hotkeyProblem(h.lobby) ? h.lobby : DEFAULT_HOTKEYS.lobby;
  const detail = h?.detail && !hotkeyProblem(h.detail) && h.detail !== lobby ? h.detail : DEFAULT_HOTKEYS.detail;
  return detail === lobby ? { ...DEFAULT_HOTKEYS } : { lobby, detail };
}
