// What each detector is called on the site, with the same names as the How it works page
// (docs/how-it-works.md, step 4), and one plain sentence on what an event of it means. The detector's
// own explanation, with its numbers, stays available under Details on each evidence card.

interface DetectorText {
  title: string;
  summary: string;
}

const DETECTORS: Record<string, DetectorText> = {
  hidden_tracking: {
    title: "Tracking a hidden enemy",
    summary: "The crosshair followed an enemy's movement while that enemy was behind a wall and unknown to the player.",
  },
  smoke_tracking: {
    title: "Tracking through smoke",
    summary: "The crosshair followed an enemy's movement inside or behind a smoke.",
  },
  flash: {
    title: "Tracking while flashed",
    summary: "The crosshair followed a moving enemy while the player was heavily blinded.",
  },
  remembered_position: {
    title: "Aim on the current hidden position",
    summary: "Aim stayed closer to where a hidden enemy actually was than to where they were last seen.",
  },
  previsibility: {
    title: "Pre-aim before visibility",
    summary: "The crosshair moved onto an enemy's real position behind cover before that enemy could be seen.",
  },
  strategic_information: {
    title: "Aim near hidden enemies",
    summary: "Over the match, the crosshair rested near hidden enemies more often than chance would give.",
  },
  information_gap: {
    title: "Following hidden enemies",
    summary: "Over the match, the crosshair kept following hidden enemies at moments when their position couldn't be known.",
  },
  smoke_kills: {
    title: "Kills through smoke",
    summary: "Many of the player's kills went through smokes.",
  },
  hidden_fire: {
    title: "Precise shots at hidden enemies",
    summary: "Over the match, the player fired right at enemies hidden behind walls or smoke, and hit them, more than chance explains.",
  },
  safe_carelessness: {
    title: "Knife out only when it is safe",
    summary: "With no enemy known, the player held the knife or bomb when no enemy was near and put it away when one actually was.",
  },
  aim_acquisition: {
    title: "Target acquisition",
    summary: "The aim moved onto a newly visible enemy unusually fast and cleanly.",
  },
  snap: {
    title: "Flick",
    summary: "A large, fast flick landed on the target with no overshoot.",
  },
  attraction: {
    title: "Aim attraction",
    summary: "Small aim corrections drifted toward nearby enemies.",
  },
  target_switch: {
    title: "Target switching",
    summary: "A very fast, large switch to a second enemy right after a kill.",
  },
  trigger_timing: {
    title: "Trigger timing",
    summary: "Shots were fired consistently within a tick or two of an enemy entering the crosshair.",
  },
  recoil: {
    title: "Recoil control",
    summary: "Spray control cancelled recoil more exactly and more repeatably than usual.",
  },
  mechanical_impossibility: {
    title: "View vs bullet direction",
    summary: "Hits landed away from where the view was pointing.",
  },
  view_integrity: {
    title: "Silent aim or anti-aim",
    summary: "The view moved in a way a mouse doesn't produce: a jump on the firing tick and back, or pitch pinned at the limit.",
  },
  input_lattice: {
    title: "Mouse-input grid",
    summary: "Aim just before shots left the step grid the player's own mouse movement follows.",
  },
  bunnyhop: {
    title: "Scripted bunnyhopping",
    summary: "The player jumped again within two ticks of landing far more often than a person manages.",
  },
};

export function detectorTitle(name: string): string {
  return DETECTORS[name]?.title ?? name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function detectorSummary(name: string): string | null {
  return DETECTORS[name]?.summary ?? null;
}
