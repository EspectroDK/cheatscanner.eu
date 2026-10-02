// Live data from Overwolf's Game Events Provider (GEP) in ow-electron.
//
// Overwolf does the game integration; this code only listens to the events Overwolf publishes. It
// never reads or changes CS2's memory (project hard rule). Without Overwolf's developer approval (or a
// published build) the "gep" package never becomes ready, and the app says so instead of failing.

import { CS2_GAME_ID, GameSource } from "./source";
import type { InfoUpdate } from "./gep";

// Minimal shapes of the ow-electron package APIs used here (full types: @overwolf/ow-electron-packages-types).
interface GepPackage {
  on(event: string, listener: (...args: any[]) => void): unknown;
  setRequiredFeatures(gameId: number, features: string[] | null): Promise<void>;
  getInfo(gameId: number): Promise<unknown>;
}
export interface OverwolfPackages {
  on(event: "ready", listener: (e: unknown, name: string, version?: string) => void): unknown;
  on(event: string, listener: (...args: any[]) => void): unknown;
  gep?: GepPackage;
}

const NOT_READY_MS = 20_000;

export class OverwolfSource extends GameSource {
  readonly kind = "overwolf" as const;
  private gep: GepPackage | null = null;
  private timer: NodeJS.Timeout | null = null;

  constructor(private packages: OverwolfPackages) {
    super();
  }

  start(): void {
    this.packages.on("ready", (_e, name) => {
      if (name === "gep") this.attach();
    });
    this.packages.on("failed-to-initialize", (_e: unknown, name: string) => {
      if (name === "gep") this.emit("problem", "Overwolf's game events failed to start. Restart the app; if it keeps happening, reinstall it.");
    });
    this.timer = setTimeout(() => {
      if (!this.gep)
        this.emit("problem",
          "Overwolf's game events aren't available yet. Until Overwolf approves the app (or a developer account is set up), the app can't see who is in your match.");
    }, NOT_READY_MS);
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
  }

  private attach(): void {
    const gep = this.packages.gep;
    if (!gep || this.gep) return;
    this.gep = gep;
    this.emit("problem", null);

    gep.on("game-detected", (e: { enable: () => void }, gameId: number) => {
      if (gameId !== CS2_GAME_ID) return;
      e.enable();
      this.setRunning(true);
      // null = every feature Overwolf offers for CS2 (roster is in match_info).
      gep.setRequiredFeatures(CS2_GAME_ID, null)
        .then(() => gep.getInfo(CS2_GAME_ID))
        .then((snapshot) => {
          this.state.applySnapshot(snapshot);
          this.scheduleMatch();
        })
        .catch((err: unknown) => this.emit("problem", `Overwolf couldn't start CS2 game events: ${String(err)}`));
    });
    gep.on("game-exit", (_e: unknown, gameId: number) => {
      if (gameId === CS2_GAME_ID) this.setRunning(false);
    });
    gep.on("new-info-update", (_e: unknown, gameId: number, data: InfoUpdate) => {
      if (gameId === CS2_GAME_ID) this.applyInfo(data);
    });
    gep.on("elevated-privileges-required", (_e: unknown, gameId: number) => {
      if (gameId === CS2_GAME_ID)
        this.emit("problem", "CS2 is running as administrator, so the app can't see it. Start Cheatscanner as administrator too, or CS2 without it.");
    });
    gep.on("error", (_e: unknown, gameId: number, error: string) => {
      if (gameId === CS2_GAME_ID) this.emit("problem", `Overwolf game events error: ${error}`);
    });
  }
}
