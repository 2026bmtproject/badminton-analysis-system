export type CapabilityKey =
  | "score"
  | "stroke"
  | "identity"
  | "cheer"
  | "highlight"
  | "commentary"
  | "court"
  | "pose"
  | "shuttle";

export type MatchCapabilities = Record<CapabilityKey, boolean>;

export type StageState = {
  status: "available" | "missing" | "error";
  message?: string;
};

export type ScoreModel = readonly [number, number];

export type CourtPositionModel = {
  x: number;
  y: number;
  coordinateSpace: "court_normalized_v1";
  source: "ankle_midpoint" | "nearby_frame_ankle_midpoint" | "single_ankle" | "bbox_bottom_center";
  confidence?: number;
  sourceFrame: number;
};

export type PositionQuality = "measured" | "estimated" | "unresolved";
export type PositionSource = CourtPositionModel["source"];

export type CourtPositionUnavailableReason =
  | "HITTER_UNRESOLVED"
  | "POSE_UNAVAILABLE"
  | "ANKLES_UNAVAILABLE"
  | "COURT_TRANSFORM_UNAVAILABLE"
  | "OUT_OF_COURT";

export type StrokeModel = {
  eventIndex: number;
  strokeIndex: number;
  frame: number;
  time: number;
  ordinal: number;
  player: string;
  type: string | null;
  confidence: number | null;
  hitter?: "a" | "b" | null;
  hitterSide?: "top" | "bottom" | null;
  courtPosition?: CourtPositionModel;
  positionQuality?: PositionQuality;
  positionSource?: PositionSource;
  positionUnavailableReason?: CourtPositionUnavailableReason;
};

export type CheerModel = {
  segmentIndex: number;
  confidence: number;
  intensity: number | null;
  windowCount: number;
};

/** One analyzed audio window, positioned at its absolute match-time center. */
export type CheerWindowModel = {
  segmentIndex: number;
  start: number;
  end: number;
  time: number;
  score: number;
};

export type EvidenceModel = {
  id: string;
  text: string;
  eventIndex: number | null;
  time: number | null;
};

export type CommentarySummaryModel = {
  segmentIndex: number;
  text: string;
  sourceFactIds: string[];
  evidence: EvidenceModel[];
};

export type CommentaryEventModel = CommentarySummaryModel & {
  strokeIndex: number;
  frame: number;
  timeSec: number;
  player: "a" | "b";
};

export type RallyCommentaryModel = {
  status: "available" | "unavailable" | "unsupported";
  source: "full-match" | "on-demand" | null;
  summary: CommentarySummaryModel | null;
  events: CommentaryEventModel[];
  unsupportedReason?: string;
};

export type CommentaryAvailabilityModel = {
  coverage: "none" | "partial" | "complete";
  availableRallyCount: number;
  unsupportedRallyCount: number;
  totalRallyCount: number;
};

export type RallyModel = {
  id: number;
  start: number;
  end: number;
  duration: number;
  score: ScoreModel | null;
  game: number | null;
  gameSource?: "scores" | "identity";
  gameConflict?: string;
  identity?: { top: "a" | "b"; bottom: "a" | "b" };
  scoreIssue?: string;
  multi: boolean;
  subScores: number[][];
  splits: number[];
  hits: StrokeModel[] | null;
  audio: CheerModel | null;
  /** Backend highlight-ranking score; never a probability or selection policy. */
  highlight: number | null;
  commentary: RallyCommentaryModel;
};

export type MatchSource = {
  matchId: string;
  importedAt: string;
  backendBaseCommit?: string;
  fingerprints: Record<string, string>;
  limitations: string[];
};

export type MatchModel = {
  players: { a: string; b: string };
  layoutOnly?: boolean;
  title: string;
  video: string;
  duration: number;
  scenario: string;
  capabilities: MatchCapabilities;
  states: Record<string, StageState>;
  rallies: RallyModel[];
  cheerTimeline?: CheerWindowModel[];
  commentaryAvailability: CommentaryAvailabilityModel;
  fps?: number;
  source?: MatchSource;
};

export type CatalogEntry = {
  id: string;
  name: string;
  url: string;
  kind: "match" | "fixture";
};

export const capabilityFromStates = (
  states: Record<string, StageState>,
  commentaryAvailable = states.commentary?.status === "available",
): MatchCapabilities => ({
  score: states.scores?.status === "available",
  stroke: states.events?.status === "available",
  identity: states.identity?.status === "available",
  cheer: states.audio_signals?.status === "available",
  highlight: states.highlights?.status === "available",
  commentary: commentaryAvailable,
  court: states.court?.status === "available",
  pose: states.pose?.status === "available",
  shuttle: states.shuttle?.status === "available",
});
