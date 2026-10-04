// Thin client for the FastAPI backend. The session cookie is sent automatically (same origin).

export type Classification = "NORMAL" | "ELEVATED" | "HIGH" | "VERY_HIGH" | "INSUFFICIENT_DATA" | string;

export interface User {
  id: number;
  steamId: string;
  personaName: string | null;
  avatarUrl: string | null;
  profileUrl: string | null;
  /** May open the Admin page (CS2A_ADMIN_STEAM_IDS on the server). */
  isAdmin?: boolean;
}

export interface MatchSummary {
  matchId: string;
  map: string | null;
  mode: string | null;
  playedAt: string | null;
  processedAt: string | null;
  processingStatus: string;
  roundsCount: number | null;
  score: Score | null;
  players: { steamId: string; name: string | null; team: number | null; classification: Classification | null }[];
}

/** Rounds won, keyed by the side each team started on ("2" = started T, "3" = started CT). */
export type Score = Record<"2" | "3", number>;

export interface Round {
  round: number;
  winner: number | null;
  winnerTeam: number | null;
  reason: number | null;
}

export interface Assessment {
  classification: Classification;
  overallEvidenceScore: number;
  axes: Record<string, number>;
  evidenceEventCount: number;
  highSeverityEventCount: number;
  encountersAnalyzed: number;
  /** Strength of the match's overall play pattern; it can lift the class without any evidence event. */
  profileStrength?: number | null;
}

/** What a player's class is made of: evidence events and the overall play pattern across matches. */
export interface PlayPattern {
  strength: number;
  percentile: number | null;
  matches: number | null;
  suddenChange: boolean;
  features: { feature: string; contribution: number; matches: number }[];
}

/** The numbers behind the play pattern (GET /players/{id}/pattern). */
export interface PatternBreakdown {
  matches: {
    matchId: string | null;
    matchVisible: boolean;
    map: string | null;
    playedAt: string | null;
    classification: Classification;
    evidenceEventCount: number;
    strength: number | null;
    cleanPercentile: number | null;
  }[];
  features: {
    feature: string;
    n: number;
    value: number;
    contribution: number;
    cheaterRanges: (number | null)[][];
    cheaterShare: number;
    cleanShare: number | null;
    cleanP10: number | null;
    cleanMedian: number | null;
    cleanP90: number | null;
    cleanPercentile: number | null;
  }[];
  reference: { source: string | null; cleanPlayers: number | null; cleanMatches: number | null } | null;
}

export interface MatchDetail {
  matchId: string;
  map: string | null;
  mode: string | null;
  playedAt: string | null;
  processedAt: string | null;
  processingStatus: string;
  error: string | null;
  /** Valve's download link for a fetched match, only while Valve still keeps the demo (about a month). */
  valveDemo?: { url: string; shareCode: string; availableUntil: string } | null;
  rounds: Round[];
  score: Score | null;
  players: {
    steamId: string;
    name: string | null;
    team: number | null;
    kills: number;
    deaths: number;
    assists: number;
    headshots: number;
    damage: number;
    rankType: number | null;
    rankNew: number | null;
    assessment: Assessment | null;
    visible: boolean;
  }[];
}

export interface Player {
  steamId: string;
  lastKnownName: string | null;
  matchesAnalyzed: number;
  assessment: {
    classification: Classification;
    historicalEvidenceScore: number;
    confidenceLevel: string;
    matchesAnalyzed: number;
    highSeverityMatches: number;
    aimScore: number;
    informationScore: number;
    triggerScore: number;
    recoilScore: number;
    eventEvidenceScore?: number;
    profile?: PlayPattern | null;
  } | null;
}

export interface EvidenceEvent {
  id: string;
  matchId: string;
  round: number | null;
  tickPeak: number;
  detector: string;
  axis: string;
  confidence: number;
  severity: number;
  explanation: string | null;
  steamId: string;
  clipUrl: string | null;
  /** Still frame of the flagged moment, shown before the clip plays. */
  posterUrl?: string | null;
  plotUrl: string | null;
  /** The other player in the moment (e.g. who was tracked). The Steam ID is only sent for players the viewer knows. */
  targetName?: string | null;
  targetSteamId?: string | null;
  /** False for a match the viewer wasn't in: the event is shown, the match page is not. */
  matchVisible?: boolean;
  map?: string | null;
  playedAt?: string | null;
}

/** One analyzed match of a player, for the timeline (all of their matches, oldest first). */
export interface TimelinePoint {
  matchId: string | null;
  matchVisible: boolean;
  map: string | null;
  playedAt: string | null;
  classification: Classification;
  overallEvidenceScore: number;
  evidenceEventCount: number;
  profileStrength?: number | null;
  axes: { aim: number; hiddenInformation: number; shotTiming: number; recoil: number };
}

export interface ApiToken {
  id: number;
  name: string | null;
  createdAt: string | null;
  lastUsedAt: string | null;
}

export interface Job {
  jobId: string;
  status: "QUEUED" | "PROCESSING" | "COMPLETED" | "DUPLICATE" | "SKIPPED" | "FAILED";
  matchId?: string;
  error?: string;
}

export interface MatchAccess {
  status: "MISSING" | "ACTIVE" | "REJECTED";
  required: boolean;
  lastShareCode?: string | null;
  lastError?: string | null;
  lastCheckedAt?: string | null;
}

export interface SteamChat {
  enabled: boolean;
  /** The bot account that sends the messages; null until the demo fetcher has checked in since the server started. */
  bot: { steamId: string; profileUrl: string } | null;
  /** Whether you are friends with the bot on Steam; null when not known yet. */
  friends: boolean | null;
  lastMessage: { matchId: string; status: "PENDING" | "SENDING" | "SENT" | "SKIPPED" | "FAILED"; error: string | null; updatedAt: string | null } | null;
}

export interface ShareCodeJob {
  shareCode: string;
  status: "QUEUED" | "FETCHING" | "DOWNLOADING" | "ANALYZING" | "DONE" | "SKIPPED" | "FAILED" | "EXPIRED";
  attempts: number;
  matchId: string | null;
  error: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface SiteInfo {
  name: string;
  domain: string;
  publicUrl: string;
  authEnabled: boolean;
  maxUploadMb: number;
  maxUploadsPerDay: number;
  contactEmail: string | null;
  /** The companion app installer, when one is on the server (site.downloads_dir). */
  companionDownload: { version: string; url: string; sizeMb: number } | null;
}

/** GET /site-stats: public usage counts for the front page, and what the scoring was calibrated on. */
export interface SiteStats {
  generatedAt: string;
  matchesAnalyzed: number;
  matchesAnalyzed7d: number;
  playersAnalyzed: number;
  roundsAnalyzed: number;
  gameMinutes: number;
  calibration: {
    datasetMatches: number;
    datasetCleanMatches: number;
    datasetLabelledCheaters: number;
    datasetMaps: number;
    proMatches: number;
    proPlayers: number;
    matchmakingDemos: number;
  };
}

type Summary = { count: number; median: number | null; p90: number | null; mean: number | null };

/** GET /admin/overview: queue, speed and usage for the admin page. */
export interface AdminOverview {
  generatedAt: string;
  users: { total: number; new7d: number; new30d: number; active24h: number; active7d: number; active30d: number; matchHistoryConnected: number };
  matches: {
    analyzed: number;
    failed: number;
    processing: number;
    analyzed24h: number;
    analyzed7d: number;
    bySource: { fetched: number; uploaded: number; other: number };
    byMap: { map: string; count: number }[];
    perDay: { date: string; count: number }[];
    queuedPerHour: { hour: string; fetched: number; uploaded: number }[];
  };
  players: {
    total: number;
    /** Players who never signed in to the site, and how many of them are in 2+, 3+, 5+ analyzed matches. */
    nonUsers: number;
    nonUsersSeenTwice: number;
    nonUsersSeen3Times: number;
    nonUsersSeen5Times: number;
    assessed: number; byClass: Record<"NORMAL" | "ELEVATED" | "HIGH" | "INSUFFICIENT_DATA", number>; byHighestMatchClass: Record<"NORMAL" | "ELEVATED" | "HIGH" | "INSUFFICIENT_DATA", number>; evidenceEvents: number };
  fetch: {
    byStatus: Record<ShareCodeJob["status"], number>;
    oldestQueuedAt: string | null;
    recentProblems: { shareCode: string; status: string; error: string | null; updatedAt: string | null }[];
    lastHistoryCheckAt: string | null;
  };
  speed: {
    analysisSeconds: Summary;
    queueWaitSeconds: Summary;
    fetchedToResultSeconds: Summary;
    /** Fetched matches, last 30 days: estimated game end to analysis done (sign-up and catch-up matches left out). */
    gameEndToAnalyzedSeconds: Summary & { skipped: { signup: number; catchUp: number; noMatchTime: number } };
    recordedSince: string;
  };
  companion: {
    linkedApps: number;
    linkedUsers: number;
    activeLast15m: number;
    active24h: number;
    active7d: number;
    lookups24h: number;
    lookups7d: number;
    lookupUsers24h: number;
    lookupsRecordedSince: string;
  };
  uploads: { attempts24h: number; attempts7d: number };
  live: { queued: number; processing: number; uploads: number; oldestWaitingSeconds: number; receivingUploads: number; workers: number };
  system: { diskFreeGb: number; diskTotalGb: number; fetcherLastSeenAt: string | null; serverTime: string };
  /** Installed map meshes against the game patch of the last 30 days' demos. */
  maps?: MapMesh[];
}

export interface MapMesh {
  map: string;
  mesh: boolean;
  renderMesh: boolean;
  meshPatch: number | null;
  meshUpdatedAt: string | null;
  latestDemoPatch: number | null;
  missing: boolean;
  stale: boolean;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  me: () => call<User>("/me"),
  myMatches: () => call<MatchSummary[]>("/me/matches"),
  match: (id: string) => call<MatchDetail>(`/matches/${encodeURIComponent(id)}`),
  player: (sid: string) => call<Player>(`/players/${sid}`),
  playerMatches: (sid: string) => call<({ matchId: string; map: string | null; playedAt: string | null; processedAt: string | null } & Assessment)[]>(`/players/${sid}/matches`),
  matchEvidence: (id: string) => call<EvidenceEvent[]>(`/matches/${encodeURIComponent(id)}/evidence`),
  playerPattern: (sid: string) => call<PatternBreakdown>(`/players/${sid}/pattern`),
  playerTimeline: (sid: string) => call<TimelinePoint[]>(`/players/${sid}/timeline`),
  siteInfo: () => call<SiteInfo>("/site-info"),
  siteStats: () => call<SiteStats>("/site-stats"),
  confirmAppLink: (userCode: string) => call<{ deviceName: string }>("/companion/pair/confirm", json("POST", { userCode })),
  playerEvidence: (sid: string) => call<EvidenceEvent[]>(`/players/${sid}/evidence?limit=50`),
  tokens: () => call<ApiToken[]>("/me/tokens"),
  createToken: (name: string) => call<ApiToken & { token: string }>("/me/tokens", json("POST", { name })),
  revokeToken: (id: number) => call<void>(`/me/tokens/${id}`, { method: "DELETE" }),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<Job>("/matches", { method: "POST", body: form });
  },
  matchAccess: () => call<MatchAccess>("/me/steam-match-access"),
  setMatchAccess: (authCode: string, knownCode: string) =>
    call<MatchAccess>("/me/steam-match-access", json("PUT", { authCode, knownCode })),
  removeMatchAccess: () => call<void>("/me/steam-match-access", { method: "DELETE" }),
  shareCodes: () => call<ShareCodeJob[]>("/me/sharecodes"),
  retryShareCode: (code: string) => call<ShareCodeJob>(`/me/sharecodes/${encodeURIComponent(code)}/retry`, { method: "POST" }),
  steamChat: () => call<SteamChat>("/me/steam-chat"),
  setSteamChat: (enabled: boolean) => call<SteamChat>("/me/steam-chat", json("PUT", { enabled })),
  adminOverview: () => call<AdminOverview>("/admin/overview"),
  job: (id: string) => call<Job>(`/jobs/${id}`),
  logout: () => fetch("/auth/logout", { method: "POST", credentials: "same-origin" }),
};
