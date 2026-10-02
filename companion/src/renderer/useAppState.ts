import { useEffect, useState } from "react";
import type { AppState } from "../shared/types";
import { bridge } from "./bridge";

export function useAppState(): AppState | null {
  const [state, setState] = useState<AppState | null>(null);
  useEffect(() => {
    let live = true;
    bridge.getState().then((s) => live && setState({ ...s }));
    const off = bridge.onState((s) => setState({ ...s }));
    return () => {
      live = false;
      off();
    };
  }, []);
  return state;
}
