import { z } from "zod";
import {
  capabilityFromStates,
  type EvidenceModel,
  type MatchModel,
  type RallyModel,
  type StageState,
} from "../src/domain/models";

export type {
  EvidenceModel as Evidence,
  MatchModel as ReviewModel,
  RallyModel as Rally,
  StageState,
  StrokeModel as Hit,
} from "../src/domain/models";

// Mirrors the reviewed Python stage records. Metadata belongs to the manifest.
const index = z.number().int().nonnegative();
const sec = z.number().nonnegative();
const probability = z.number().min(0).max(1);
const segment = z.object({
  start_frame: index,
  end_frame: index,
  start_sec: sec,
  end_sec: sec,
  duration_sec: sec,
});
const score = z.object({
  segment_index: index,
  score_a: index.nullable(),
  score_b: index.nullable(),
  game_index: index.nullish(),
  server: z.enum(["a", "b"]).nullish(),
  sub_scores: z.array(z.tuple([index, index])).nullish(),
  split_secs: z.array(sec).nullish(),
});
const stroke = z.object({
  event_index: index,
  frame: index,
  segment_index: index,
  player: z.enum(["top", "bottom"]).nullable(),
  stroke_type: z.string(),
  confidence: probability,
});
const signal = z.object({
  segment_index: index,
  cheer_confidence: probability,
  cheer_intensity: probability.nullable(),
  n_cheer_windows: index,
});
const commentaryText = z
  .string()
  .min(1)
  .refine((value) => /\S/.test(value));
const sourceFactIds = z.array(commentaryText).min(1);
const summary = z
  .object({
    segment_index: index,
    text: commentaryText,
    source_fact_ids: sourceFactIds,
  })
  .strict();
const commentEvent = summary
  .extend({
    stroke_index: index,
    frame: index,
    time_sec: sec,
    player: z.enum(["a", "b"]),
  })
  .strict();
const commentary = z
  .object({
    segment_index: index,
    events: z.array(commentEvent),
    summary: summary.nullable(),
  })
  .strict();
const metadata = z.record(z.string(), z.unknown());
const unsupportedSegment = z
  .object({ segment_index: index, reason: commentaryText })
  .strict();
const fullCommentaryArtifact = z
  .object({
    schema_version: z.literal("commentary-rallies-v1"),
    rallies: z.array(commentary),
    unsupported_segments: z.array(unsupportedSegment),
    diagnostics: z.array(metadata),
    runtime: metadata,
  })
  .strict();
const segmentCommentaryArtifact = z
  .object({
    schema_version: z.literal("commentary-segment-v1"),
    segment_index: index,
    rally: commentary,
    identity: metadata,
    tactical: metadata,
    commentary: metadata,
    runtime: metadata,
  })
  .strict();
export const manifestSchema = z.object({
  title: z.string(),
  video: z.string(),
  duration: sec,
  players: z.object({ a: z.string(), b: z.string() }),
  identities: z.record(
    z.string(),
    z.object({ top: z.enum(["a", "b"]), bottom: z.enum(["a", "b"]) }),
  ),
  scenarios: z.array(
    z.object({ id: z.string(), name: z.string(), stages: z.array(z.string()) }),
  ),
});
export type Manifest = z.infer<typeof manifestSchema>;
export type Input = Record<string, unknown>;
const fail = (message: string): never => {
  throw new Error(message);
};
const requireThat = (condition: boolean, message: string) => {
  if (!condition) fail(message);
};

export function adapt(
  raw: Input,
  manifest: Manifest,
  scenario: string,
): MatchModel {
  const states: Record<string, StageState> = {};
  function read<T>(name: string, schema: z.ZodType<T>): T | null {
    if (raw[name] === undefined) {
      states[name] = { status: "missing" };
      return null;
    }
    const parsed = schema.safeParse(raw[name]);
    if (!parsed.success) {
      states[name] = { status: "error", message: "格式或讀取失敗" };
      return null;
    }
    states[name] = { status: "available" };
    return parsed.data;
  }
  function checked<T>(
    name: string,
    value: T | null,
    check: (v: T) => void,
  ): T | null {
    if (value === null) return null;
    try {
      check(value);
      return value;
    } catch (e) {
      states[name] = { status: "error", message: String(e) };
      return null;
    }
  }
  const sg = read(
    "segments",
    z.object({ fps: z.number().positive(), segments: z.array(segment).min(1) }),
  );
  if (!sg) fail("必要的 segments 無法讀取");
  const { segments, fps } = sg!;
  segments.forEach((s, i) => {
    requireThat(
      s.end_frame >= s.start_frame &&
        s.end_sec <= manifest.duration &&
        (i === 0 || s.start_frame > segments[i - 1].end_frame),
      "片段順序或範圍不合法",
    );
    requireThat(
      Math.abs(s.start_sec - s.start_frame / fps) < 0.0011 &&
        Math.abs(s.end_sec - s.end_frame / fps) < 0.0011 &&
        Math.abs(s.duration_sec - (s.end_sec - s.start_sec)) < 0.0011,
      "片段影格與時間不一致",
    );
  });
  for (const mapping of Object.values(manifest.identities))
    requireThat(mapping.top !== mapping.bottom, "身分映射必須不同");
  const locate = (frame: number) =>
    segments.findIndex((s) => frame >= s.start_frame && frame <= s.end_frame);
  function uniqueSegments(rows: { segment_index: number }[]) {
    requireThat(
      new Set(rows.map((s) => s.segment_index)).size === rows.length,
      "重複片段索引",
    );
    requireThat(
      rows.every((s) => s.segment_index < segments.length),
      "未知片段索引",
    );
  }
  const scores = checked(
    "scores",
    read(
      "scores",
      z.object({
        rallies: z.array(score),
        attempts: z
          .array(
            z.object({
              segment_index: index,
              method: z.string().optional(),
              attempts: z.string().optional(),
              note: z.string().optional(),
            }),
          )
          .optional(),
      }),
    ),
    (v) => {
      uniqueSegments(v.rallies);
      v.rallies.forEach((s) => {
        requireThat(
          (s.score_a === null) === (s.score_b === null),
          "比分缺值必須成對",
        );
        if (s.sub_scores != null || s.split_secs != null) {
          requireThat(
            !!s.sub_scores &&
              !!s.split_secs &&
              s.sub_scores.length >= 2 &&
              s.split_secs.length === s.sub_scores.length - 1,
            "多回合比分資料不完整",
          );
          const last = s.sub_scores!.at(-1)!;
          requireThat(
            last[0] === s.score_a && last[1] === s.score_b,
            "最終比分不一致",
          );
          s.split_secs!.forEach((t, i) =>
            requireThat(
              t >= segments[s.segment_index].start_sec &&
                t <= segments[s.segment_index].end_sec &&
                (i === 0 || t > s.split_secs![i - 1]),
              "比分變動時間不合法",
            ),
          );
        }
      });
    },
  );
  const identity = checked(
    "identity",
    read(
      "identity",
      z.object({
        epochs: z.array(
          z.object({
            first_segment: index,
            last_segment: index,
            game_index: index,
            top: z.enum(["a", "b"]),
            bottom: z.enum(["a", "b"]),
          }),
        ),
      }),
    ),
    (v) => {
      const occupied = new Set<number>();
      for (const e of v.epochs) {
        requireThat(
          e.first_segment <= e.last_segment && e.last_segment < segments.length,
          "身分區間超出範圍",
        );
        requireThat(e.top !== e.bottom, "身分映射必須不同");
        for (let i = e.first_segment; i <= e.last_segment; i++) {
          requireThat(!occupied.has(i), "身分區間重疊");
          occupied.add(i);
        }
      }
    },
  );
  const events = checked(
    "events",
    read("events", z.object({ events: z.array(z.object({ frame: index })) })),
    (v) => {
      v.events.forEach((e, i) =>
        requireThat(
          locate(e.frame) >= 0 && (i === 0 || e.frame > v.events[i - 1].frame),
          "擊球順序或範圍不合法",
        ),
      );
    },
  );
  const strokes = checked(
    "strokes",
    read("strokes", z.object({ strokes: z.array(stroke) })),
    (v) => {
      requireThat(
        !!events && v.strokes.length === events.events.length,
        "strokes / events 數量不一致",
      );
      v.strokes.forEach((s, i) =>
        requireThat(
          s.event_index === i &&
            s.frame === events!.events[i].frame &&
            locate(s.frame) === s.segment_index,
          "擊球 ID / 影格 / 片段不一致",
        ),
      );
    },
  );
  const audio = checked(
    "audio_signals",
    read("audio_signals", z.object({ signals: z.array(signal) })),
    (v) => {
      uniqueSegments(v.signals);
      v.signals.forEach((s) =>
        requireThat(
          (s.n_cheer_windows === 0) === (s.cheer_intensity === null),
          "歡呼支持數不一致",
        ),
      );
    },
  );
  const highlights = checked(
    "highlights",
    read(
      "highlights",
      z.object({
        highlights: z.array(
          z.object({ segment_index: index, score: probability }),
        ),
      }),
    ),
    (v) => {
      uniqueSegments(v.highlights);
      v.highlights.forEach((h) =>
        requireThat(
          !!audio?.signals.find((s) => s.segment_index === h.segment_index),
          "排序缺少來源音訊觀察",
        ),
      );
    },
  );
  function validateCommentaryRally(c: z.infer<typeof commentary>) {
    requireThat(c.segment_index < segments.length, "賽評片段索引不存在");
    requireThat(
      !c.summary || c.summary.segment_index === c.segment_index,
      "賽評摘要片段不一致",
    );
    const strokeIndexes = new Set<number>();
    c.events.forEach((e, i) => {
      const sourceStroke = strokes?.strokes.find(
        (candidate) => candidate.event_index === e.stroke_index,
      );
      const previous = c.events[i - 1];
      const ordered =
        !previous ||
        e.time_sec > previous.time_sec ||
        (e.time_sec === previous.time_sec && e.frame > previous.frame) ||
        (e.time_sec === previous.time_sec &&
          e.frame === previous.frame &&
          e.stroke_index > previous.stroke_index);
      requireThat(
        e.segment_index === c.segment_index &&
          !!events &&
          !!sourceStroke &&
          sourceStroke.segment_index === c.segment_index &&
          sourceStroke.frame === e.frame &&
          events.events[e.stroke_index]?.frame === e.frame &&
          locate(e.frame) === c.segment_index &&
          Math.abs(e.time_sec - e.frame / fps) < 0.0011 &&
          ordered &&
          (!previous || e.frame >= previous.frame) &&
          !strokeIndexes.has(e.stroke_index),
        "賽評來源 ID、片段或時間不一致",
      );
      strokeIndexes.add(e.stroke_index);
    });
  }
  const fullCommentary = checked(
    "commentary",
    read("commentary", fullCommentaryArtifact),
    (value) => {
      uniqueSegments(value.rallies);
      value.rallies.forEach(validateCommentaryRally);
      uniqueSegments(value.unsupported_segments);
      value.unsupported_segments.forEach((item) =>
        requireThat(
          !value.rallies.some(
            (rally) => rally.segment_index === item.segment_index,
          ),
          "賽評片段不可同時標記為可用與不支援",
        ),
      );
    },
  );
  const onDemandCommentary = checked(
    "commentary_segments",
    read("commentary_segments", z.array(segmentCommentaryArtifact)),
    (artifacts) => {
      requireThat(
        new Set(artifacts.map((item) => item.segment_index)).size ===
          artifacts.length,
        "重複的隨選賽評片段",
      );
      artifacts.forEach((artifact) => {
        requireThat(
          artifact.segment_index === artifact.rally.segment_index,
          "隨選賽評片段不一致",
        );
        validateCommentaryRally(artifact.rally);
      });
    },
  );
  const commentaryBySegment = new Map<
    number,
    {
      source: "full-match" | "on-demand";
      rally: z.infer<typeof commentary>;
    }
  >();
  const unsupportedBySegment = new Map<number, string>();
  fullCommentary?.rallies.forEach((rally) =>
    commentaryBySegment.set(rally.segment_index, {
      source: "full-match",
      rally,
    }),
  );
  fullCommentary?.unsupported_segments.forEach((item) =>
    unsupportedBySegment.set(item.segment_index, item.reason),
  );
  onDemandCommentary?.forEach((artifact) => {
    commentaryBySegment.set(artifact.segment_index, {
      source: "on-demand",
      rally: artifact.rally,
    });
    unsupportedBySegment.delete(artifact.segment_index);
  });
  const rallies = segments.map((s, id): RallyModel => {
    const sc = scores?.rallies.find((s) => s.segment_index === id);
    const epoch = identity?.epochs.find(
      (e) => id >= e.first_segment && id <= e.last_segment,
    );
    const mapping =
      epoch ??
      (raw.identity === undefined
        ? manifest.identities[String(id)]
        : undefined);
    const gameConflict =
      sc?.game_index != null && epoch && sc.game_index !== epoch.game_index
        ? `局數衝突：scores 第 ${sc.game_index + 1} 局；identity 推導第 ${epoch.game_index + 1} 局`
        : undefined;
    // Do not turn adjacent score observations into a before/after transition.
    // The backend contract guarantees only this segment's final score.
    const segmentScore: [number, number] | null =
      sc?.score_a != null && sc.score_b != null
        ? [sc.score_a, sc.score_b]
        : null;
    const hits = events
      ? events.events
          .flatMap((event, eventIndex) => {
            if (locate(event.frame) !== id) return [];
            const st = strokes?.strokes[eventIndex];
            const identity = st?.player ? mapping?.[st.player] : undefined;
            const player = identity
              ? manifest.players[identity]
              : st?.player === "top"
                ? "畫面上方"
                : st?.player === "bottom"
                  ? "畫面下方"
                  : "未知球員";
            return [
              {
                eventIndex,
                strokeIndex: eventIndex,
                frame: event.frame,
                time: event.frame / fps,
                ordinal: 0,
                player,
                type: st?.stroke_type ?? null,
                confidence: st?.confidence ?? null,
                hitter: identity ?? null,
                hitterSide: st?.player ?? null,
              },
            ];
          })
          .map((h, i) => ({ ...h, ordinal: i + 1 }))
      : null;
    // Only exact canonical observation IDs are resolvable from these stages.
    // Pattern/court/trajectory IDs require their own supplied fact artifacts.
    function evidence(ref: string): EvidenceModel {
      const hit = hits?.find(
        (h) => ref === `rally:${id}:stroke:${h.eventIndex}`,
      );
      if (!hit)
        return { id: ref, text: "無法取得依據", eventIndex: null, time: null };
      return {
        id: ref,
        text: `第 ${hit.ordinal} 拍 · ${hit.player} · ${hit.type ?? "未提供球種"}（來源擊球觀察）`,
        eventIndex: hit.eventIndex,
        time: hit.time,
      };
    }
    const commentaryRecord = commentaryBySegment.get(id);
    const unsupportedReason = unsupportedBySegment.get(id);
    return {
      id,
      start: s.start_sec,
      end: s.end_sec,
      duration: s.duration_sec,
      score: segmentScore,
      game: sc?.game_index ?? epoch?.game_index ?? null,
      gameSource:
        sc?.game_index != null ? "scores" : epoch ? "identity" : undefined,
      gameConflict,
      identity: mapping
        ? { top: mapping.top, bottom: mapping.bottom }
        : undefined,
      scoreIssue: segmentScore
        ? undefined
        : scores?.attempts?.find((a) => a.segment_index === id)?.note,
      multi: !!sc?.sub_scores?.length,
      subScores: sc?.sub_scores ?? [],
      splits: sc?.split_secs ?? [],
      hits,
      audio: (() => {
        const signal = audio?.signals.find((item) => item.segment_index === id);
        return signal
          ? {
              segmentIndex: signal.segment_index,
              confidence: signal.cheer_confidence,
              intensity: signal.cheer_intensity,
              windowCount: signal.n_cheer_windows,
            }
          : null;
      })(),
      highlight:
        highlights?.highlights.find((h) => h.segment_index === id)?.score ??
        null,
      commentary: commentaryRecord
        ? {
            status: "available",
            source: commentaryRecord.source,
            summary: commentaryRecord.rally.summary
              ? {
                  segmentIndex: commentaryRecord.rally.summary.segment_index,
                  text: commentaryRecord.rally.summary.text,
                  sourceFactIds: commentaryRecord.rally.summary.source_fact_ids,
                  evidence:
                    commentaryRecord.rally.summary.source_fact_ids.map(
                      evidence,
                    ),
                }
              : null,
            events: commentaryRecord.rally.events.map((event) => ({
              segmentIndex: event.segment_index,
              strokeIndex: event.stroke_index,
              frame: event.frame,
              timeSec: event.time_sec,
              player: event.player,
              text: event.text,
              sourceFactIds: event.source_fact_ids,
              evidence: event.source_fact_ids.map(evidence),
            })),
          }
        : {
            status: unsupportedReason ? "unsupported" : "unavailable",
            source: null,
            summary: null,
            events: [],
            ...(unsupportedReason ? { unsupportedReason } : {}),
          },
    };
  });
  const availableRallyCount = commentaryBySegment.size;
  const commentaryAvailability = {
    coverage:
      availableRallyCount === 0
        ? ("none" as const)
        : availableRallyCount === rallies.length
          ? ("complete" as const)
          : ("partial" as const),
    availableRallyCount,
    unsupportedRallyCount: unsupportedBySegment.size,
    totalRallyCount: rallies.length,
  };
  return {
    players: { ...manifest.players },
    title: manifest.title,
    video: manifest.video,
    duration: manifest.duration,
    scenario,
    capabilities: capabilityFromStates(states, availableRallyCount > 0),
    states,
    rallies,
    commentaryAvailability,
    fps,
  };
}

/** Fixture authoring is strict; production adapt() preserves optional errors. */
export function validateFixture(model: MatchModel): MatchModel {
  if (Object.values(model.states).some((s) => s.status === "error"))
    throw new Error(
      `Invalid declared fixture: ${JSON.stringify(model.states)}`,
    );
  return model;
}
