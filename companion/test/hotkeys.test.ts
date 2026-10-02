import { describe, expect, it } from "vitest";
import { cleanHotkeys, hotkeyFromEvent, hotkeyProblem } from "../src/shared/hotkeys";

const ev = (code: string, mods: Partial<{ ctrlKey: boolean; altKey: boolean; shiftKey: boolean }> = {}) =>
  ({ code, ctrlKey: false, altKey: false, shiftKey: false, ...mods });

describe("hotkeys", () => {
  it("turns key presses into accelerators", () => {
    expect(hotkeyFromEvent(ev("F7"))).toBe("F7");
    expect(hotkeyFromEvent(ev("F2", { shiftKey: true }))).toBe("Shift+F2");
    expect(hotkeyFromEvent(ev("KeyK", { ctrlKey: true, altKey: true }))).toBe("Ctrl+Alt+K");
    expect(hotkeyFromEvent(ev("Numpad5", { altKey: true }))).toBe("Alt+num5");
    expect(hotkeyFromEvent(ev("ShiftLeft", { shiftKey: true }))).toBeNull();
    expect(hotkeyFromEvent(ev("Space"))).toBeNull();
  });

  it("only accepts keys that don't get in the way of typing", () => {
    for (const ok of ["F7", "Shift+F2", "Insert", "Ctrl+K", "Alt+5", "Ctrl+Shift+PageUp"]) expect(hotkeyProblem(ok)).toBeNull();
    for (const bad of ["K", "Shift+K", "5", "Win+F7", "Ctrl+Ctrl+F7", "Space", ""]) expect(hotkeyProblem(bad)).not.toBeNull();
  });

  it("cleans stored hotkeys", () => {
    expect(cleanHotkeys(undefined)).toEqual({ lobby: "Shift+F2", detail: "F7" });
    expect(cleanHotkeys({ lobby: "F8", detail: "K" })).toEqual({ lobby: "F8", detail: "F7" });
    expect(cleanHotkeys({ lobby: "F7" })).toEqual({ lobby: "Shift+F2", detail: "F7" });
    expect(cleanHotkeys({ lobby: "F7", detail: "F7" })).toEqual({ lobby: "Shift+F2", detail: "F7" });
  });
});
