import { z } from "zod";
import { capabilityFromStates, type MatchModel } from "../domain/models";
import { COURT_ADJACENT_MARGIN_M, distanceOutsideCourtM } from "../domain/courtPosition";

const stageState = z.object({
  status: z.enum(["available", "missing", "error"]),
  message: z.string().optional(),
});

const stroke = z.object({
  eventIndex: z.number().int().nonnegative(),
  strokeIndex: z.number().int().nonnegative(),
  frame: z.number().int().nonnegative(),
  time: z.number().nonnegative(),
  ordinal: z.number().int().positive(),
  player: z.string(),
  type: z.string().nullable(),
  confidence: z.number().nullable(),
  hitter: z.enum(["a", "b"]).nullable().optional(),
  hitterSide: z.enum(["top", "bottom"]).nullable().optional(),
  courtPosition: z.object({
    x: z.number().finite(),
    y: z.number().finite(),
    coordinateSpace: z.literal("court_normalized_v1"),
    source: z.enum(["ankle_midpoint", "nearby_frame_ankle_midpoint", "single_ankle", "bbox_bottom_center"]),
    confidence: z.number().min(0).max(1).optional(),
    sourceFrame: z.number().int().nonnegative(),
  }).optional(),
  positionQuality: z.enum(["measured", "estimated", "unresolved"]).optional(),
  positionSource: z.enum(["ankle_midpoint", "nearby_frame_ankle_midpoint", "single_ankle", "bbox_bottom_center"]).optional(),
  positionUnavailableReason: z.enum([
    "HITTER_UNRESOLVED", "POSE_UNAVAILABLE", "ANKLES_UNAVAILABLE",
    "COURT_TRANSFORM_UNAVAILABLE", "OUT_OF_COURT",
  ]).optional(),
});

const canonicalCheer = z.object({
  segmentIndex: z.number().int().nonnegative(),
  confidence: z.number(),
  intensity: z.number().nullable(),
  windowCount: z.number().int().nonnegative(),
});

// Compatibility belongs at the cache parsing boundary. Everything returned
// from this module uses the canonical frontend model.
const legacyCheer = z
  .object({
    segment_index: z.number().int().nonnegative(),
    cheer_confidence: z.number(),
    cheer_intensity: z.number().nullable(),
    n_cheer_windows: z.number().int().nonnegative(),
  })
  .transform((signal) => ({
    segmentIndex: signal.segment_index,
    confidence: signal.cheer_confidence,
    intensity: signal.cheer_intensity,
    windowCount: signal.n_cheer_windows,
  }));

const cheer = z.union([canonicalCheer, legacyCheer]);

const evidence = z.object({
  id: z.string(),
  text: z.string(),
  eventIndex: z.number().int().nullable(),
  time: z.number().nullable(),
});

const legacyCommentary = z.object({
  text: z.string(),
  eventIndex: z.number().int().nullable(),
  evidence: z.array(evidence),
});

const commentarySummary = z.object({
  segmentIndex: z.number().int().nonnegative(),
  text: z.string().min(1),
  sourceFactIds: z.array(z.string().min(1)).min(1),
  evidence: z.array(evidence),
});

const commentaryEvent = commentarySummary.extend({
  strokeIndex: z.number().int().nonnegative(),
  frame: z.number().int().nonnegative(),
  timeSec: z.number().nonnegative(),
  player: z.enum(["a", "b"]),
});

const rallyCommentary = z.object({
  status: z.enum(["available", "unavailable", "unsupported"]),
  source: z.enum(["full-match", "on-demand"]).nullable(),
  summary: commentarySummary.nullable(),
  events: z.array(commentaryEvent),
  unsupportedReason: z.string().optional(),
});

const scorePair = z.tuple([
  z.number().int().nonnegative(),
  z.number().int().nonnegative(),
]);

const rally = z
  .object({
    id: z.number().int().nonnegative(),
    start: z.number().nonnegative(),
    end: z.number().nonnegative(),
    duration: z.number().nonnegative(),
    score: scorePair.nullable(),
    game: z.number().int().nonnegative().nullable(),
    gameSource: z.enum(["scores", "identity"]).optional(),
    gameConflict: z.string().optional(),
    identity: z
      .object({ top: z.enum(["a", "b"]), bottom: z.enum(["a", "b"]) })
      .optional(),
    scoreIssue: z.string().optional(),
    multi: z.boolean(),
    subScores: z.array(scorePair),
    splits: z.array(z.number()),
    hits: z.array(stroke).nullable(),
    audio: cheer.nullable(),
    highlight: z.number().nullable(),
    commentary: rallyCommentary.optional(),
    comments: z.array(legacyCommentary).optional(),
  })
  .superRefine((item, context) => {
    if (item.commentary && item.comments) {
      context.addIssue({
        code: "custom",
        path: ["commentary"],
        message: "Rally cannot mix canonical and legacy commentary",
      });
    }
    if (item.comments?.length) {
      context.addIssue({
        code: "custom",
        path: ["comments"],
        message: "Non-empty draft commentary cache must be regenerated",
      });
    }
  })
  .transform(({ comments: _comments, ...item }) => ({
    ...item,
    commentary: item.commentary ?? {
      status: "unavailable" as const,
      source: null,
      summary: null,
      events: [],
    },
  }));

const commentaryAvailability = z.object({
  coverage: z.enum(["none", "partial", "complete"]),
  availableRallyCount: z.number().int().nonnegative(),
  unsupportedRallyCount: z.number().int().nonnegative(),
  totalRallyCount: z.number().int().nonnegative(),
});

const source = z.object({
  matchId: z.string(),
  importedAt: z.string(),
  backendBaseCommit: z.string().optional(),
  fingerprints: z.record(z.string(), z.string()),
  limitations: z.array(z.string()),
  outputs: z
    .array(z.object({ name: z.string(), url: z.string(), kind: z.string() }))
    .optional(),
});

const TIMELINE_EPSILON_SEC = 1e-6;

const matchEnvelope = z
  .object({
    title: z.string(),
    video: z.string(),
    duration: z.number().positive(),
    scenario: z.string(),
    players: z.object({ a: z.string(), b: z.string() }),
    states: z.record(z.string(), stageState),
    rallies: z.array(rally),
    commentaryAvailability: commentaryAvailability.optional(),
    layoutOnly: z.boolean().optional(),
    fps: z.number().positive().optional(),
    source: source.optional(),
  })
  .superRefine((match, context) => {
    const rallyIds = new Set<number>();
    const eventIndexes = new Set<number>();
    match.rallies.forEach((item, rallyIndex) => {
      if (rallyIds.has(item.id)) {
        context.addIssue({
          code: "custom",
          path: ["rallies", rallyIndex, "id"],
          message: "Rally IDs must be unique within a match",
        });
      }
      rallyIds.add(item.id);
      if (item.end < item.start) {
        context.addIssue({
          code: "custom",
          path: ["rallies", rallyIndex, "end"],
          message: "Rally end must not precede start",
        });
      }
      if (item.end > match.duration + TIMELINE_EPSILON_SEC) {
        context.addIssue({
          code: "custom",
          path: ["rallies", rallyIndex, "end"],
          message: "Rally must remain within match duration",
        });
      }
      if (item.audio && item.audio.segmentIndex !== item.id) {
        context.addIssue({
          code: "custom",
          path: ["rallies", rallyIndex, "audio", "segmentIndex"],
          message: "Audio segment must belong to its Rally",
        });
      }
      item.hits?.forEach((hit, strokeIndex) => {
        if (hit.courtPosition &&
          distanceOutsideCourtM(hit.courtPosition.x, hit.courtPosition.y) > COURT_ADJACENT_MARGIN_M) {
          context.addIssue({
            code: "custom",
            path: ["rallies", rallyIndex, "hits", strokeIndex, "courtPosition"],
            message: "Court position exceeds the reviewed adjacent band",
          });
        }
        const frameDelta = hit.courtPosition ? Math.abs(hit.courtPosition.sourceFrame - hit.frame) : 0;
        if (hit.courtPosition &&
          (hit.positionUnavailableReason || (!hit.hitter && !hit.hitterSide) ||
           hit.positionQuality === "unresolved" ||
           (hit.positionSource && hit.positionSource !== hit.courtPosition.source) ||
           (hit.positionQuality === "measured" &&
             (hit.courtPosition.source !== "ankle_midpoint" || frameDelta !== 0)) ||
           (frameDelta > 0 &&
             (hit.courtPosition.source !== "nearby_frame_ankle_midpoint" && hit.courtPosition.source !== "single_ankle" ||
              !match.fps || frameDelta / match.fps > 0.12 + TIMELINE_EPSILON_SEC)))) {
          context.addIssue({
            code: "custom",
            path: ["rallies", rallyIndex, "hits", strokeIndex, "courtPosition"],
            message: "Court position must come from the hitter within its source window",
          });
        }
        if (eventIndexes.has(hit.eventIndex)) {
          context.addIssue({
            code: "custom",
            path: ["rallies", rallyIndex, "hits", strokeIndex, "eventIndex"],
            message: "Stroke eventIndex must be globally unique",
          });
        }
        eventIndexes.add(hit.eventIndex);
        if (
          hit.time < item.start - TIMELINE_EPSILON_SEC ||
          hit.time > item.end + TIMELINE_EPSILON_SEC
        ) {
          context.addIssue({
            code: "custom",
            path: ["rallies", rallyIndex, "hits", strokeIndex, "time"],
            message: "Stroke time must lie within its Rally",
          });
        }
      });
      const commentary = item.commentary;
      if (
        (commentary.status === "available") !==
        (commentary.source !== null)
      ) {
        context.addIssue({
          code: "custom",
          path: ["rallies", rallyIndex, "commentary", "source"],
          message: "Commentary availability requires an artifact source",
        });
      }
      if (
        commentary.status !== "available" &&
        (commentary.summary !== null || commentary.events.length > 0)
      ) {
        context.addIssue({
          code: "custom",
          path: ["rallies", rallyIndex, "commentary"],
          message: "Unavailable commentary cannot contain presentation data",
        });
      }
      if (
        commentary.status === "unsupported" &&
        !commentary.unsupportedReason
      ) {
        context.addIssue({
          code: "custom",
          path: ["rallies", rallyIndex, "commentary", "unsupportedReason"],
          message: "Unsupported commentary requires a reason",
        });
      }
      if (commentary.summary && commentary.summary.segmentIndex !== item.id) {
        context.addIssue({
          code: "custom",
          path: ["rallies", rallyIndex, "commentary", "summary"],
          message: "Commentary summary must belong to its Rally",
        });
      }
      const commentaryStrokeIndexes = new Set<number>();
      commentary.events.forEach((event, commentaryIndex) => {
        const sourceStroke = item.hits?.find(
          (hit) => hit.eventIndex === event.strokeIndex,
        );
        if (
          event.segmentIndex !== item.id ||
          !sourceStroke ||
          sourceStroke.frame !== event.frame ||
          Math.abs(sourceStroke.time - event.timeSec) > TIMELINE_EPSILON_SEC
        ) {
          context.addIssue({
            code: "custom",
            path: [
              "rallies",
              rallyIndex,
              "commentary",
              "events",
              commentaryIndex,
            ],
            message: "Commentary event must join its canonical Stroke",
          });
        }
        if (commentaryStrokeIndexes.has(event.strokeIndex)) {
          context.addIssue({
            code: "custom",
            path: [
              "rallies",
              rallyIndex,
              "commentary",
              "events",
              commentaryIndex,
              "strokeIndex",
            ],
            message: "Commentary Stroke references must be unique per Rally",
          });
        }
        commentaryStrokeIndexes.add(event.strokeIndex);
      });
    });
  });

export function parseMatchModel(input: unknown): MatchModel {
  const parsed = matchEnvelope.parse(input);
  const availableRallyCount = parsed.rallies.filter(
    (item) => item.commentary.status === "available",
  ).length;
  const unsupportedRallyCount = parsed.rallies.filter(
    (item) => item.commentary.status === "unsupported",
  ).length;
  const derivedAvailability = {
    coverage:
      availableRallyCount === 0
        ? ("none" as const)
        : availableRallyCount === parsed.rallies.length
          ? ("complete" as const)
          : ("partial" as const),
    availableRallyCount,
    unsupportedRallyCount,
    totalRallyCount: parsed.rallies.length,
  };
  if (
    parsed.commentaryAvailability &&
    JSON.stringify(parsed.commentaryAvailability) !==
      JSON.stringify(derivedAvailability)
  ) {
    throw new Error("Commentary availability metadata does not match Rallies");
  }
  return {
    ...parsed,
    commentaryAvailability: derivedAvailability,
    capabilities: capabilityFromStates(parsed.states, availableRallyCount > 0),
  };
}
