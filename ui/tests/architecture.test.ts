import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { nextTick, ref } from "vue";
import {
  capabilityFromStates,
  type MatchModel,
  type RallyModel,
} from "../src/domain/models";
import { parseMatchModel } from "../src/data/matchParser";
import {
  activeRallyAt,
  useReviewWorkspace,
} from "../src/state/useReviewWorkspace";
import {
  fitViewport,
  matchViewport,
  percentToTime,
  rallyViewport,
  timeToPercent,
  visibleRallies,
} from "../src/temporal/timeline";
import { playerShortcutAction, repeatsWhileHeld } from "../src/interaction/playerShortcuts";
import {
  edgeLabelSide,
  nearestTemporalMark,
  percentOfDuration,
  timeFromClientX,
} from "../src/interaction/temporalInteraction";
import {
  formatPreciseTime,
  formatPreciseTimeParts,
  formatTimelineAxisTime,
  scoreText,
} from "../src/format";

const rally = (id: number, start: number, end: number): RallyModel => ({
  id,
  start,
  end,
  duration: end - start,
  score: null,
  game: null,
  multi: false,
  subScores: [],
  splits: [],
  hits: [],
  audio: null,
  highlight: null,
  commentary: {
    status: "unavailable",
    source: null,
    summary: null,
    events: [],
  },
});

const rawMatch = (rallies: RallyModel[] = [rally(0, 1, 2)]) => ({
  title: "Parser test",
  video: "/video",
  duration: 10,
  scenario: "parser",
  players: { a: "A", b: "B" },
  states: { segments: { status: "available" as const } },
  rallies,
  commentaryAvailability: {
    coverage: "none" as const,
    availableRallyCount: 0,
    unsupportedRallyCount: 0,
    totalRallyCount: rallies.length,
  },
});

test("capabilities reflect artifact state without fabricated defaults", () => {
  const capabilities = capabilityFromStates({
    events: { status: "available" },
    scores: { status: "missing" },
    audio_signals: { status: "error", message: "invalid optional artifact" },
    highlights: { status: "available" },
  });
  assert.equal(capabilities.stroke, true);
  assert.equal(capabilities.highlight, true);
  assert.equal(capabilities.score, false);
  assert.equal(capabilities.cheer, false);
  assert.equal(capabilities.commentary, false);
});

test("video time is the sole source for active rally context", () => {
  const rallies = [rally(7, 10, 12), rally(12, 14, 17)];
  assert.equal(activeRallyAt(rallies, 11)?.id, 7);
  assert.equal(activeRallyAt(rallies, 13), null);
  assert.equal(activeRallyAt(rallies, 16)?.id, 12);
});

test("all timeline tracks share reversible absolute-time mapping", () => {
  const view = matchViewport(3_600);
  for (const time of [0, 423.56, 1_800, 3_600]) {
    assert.ok(
      Math.abs(percentToTime(timeToPercent(time, view), view) - time) < 1e-9,
    );
  }
  const selected = rally(12, 423.56, 436.32);
  const focus = rallyViewport(selected);
  assert.equal(timeToPercent(selected.start, focus), 0);
  assert.equal(timeToPercent(selected.end, focus), 100);
  assert.deepEqual(visibleRallies([rally(1, 0, 1), selected], focus), [
    selected,
  ]);
});

test("Fit Rally requires a selection and returns to the full-match viewport", () => {
  const selected = rally(12, 423.56, 436.32);
  assert.deepEqual(fitViewport("rally", 3_893, null), matchViewport(3_893));
  assert.deepEqual(fitViewport("rally", 3_893, selected), {
    startSec: 423.56,
    endSec: 436.32,
    durationSec: 12.759999999999991,
  });
  assert.deepEqual(fitViewport("match", 3_893, selected), matchViewport(3_893));
});

test("timeline pointer mapping and sparse hit testing share the active viewport", () => {
  const viewport = { startSec: 100, endSec: 120, durationSec: 20 };
  assert.equal(timeFromClientX(250, 50, 400, viewport), 110);
  assert.equal(timeFromClientX(-10, 50, 400, viewport), 100);
  assert.equal(timeFromClientX(900, 50, 400, viewport), 120);
  const marks = [{ time: 104 }, { time: 112 }];
  assert.equal(
    nearestTemporalMark(marks, 112.2, (mark) => mark.time, viewport, 400)?.time,
    112,
  );
  assert.equal(
    nearestTemporalMark(marks, 109, (mark) => mark.time, viewport, 400),
    null,
  );
});

test("global scrubber registrations preserve canonical Rally and Stroke time", () => {
  const duration = 3893.893515;
  const start = percentOfDuration(423.56, duration);
  const end = percentOfDuration(436.32, duration);
  const stroke = percentOfDuration(430.2, duration);
  const midpoint = (start + end) / 2;
  assert.ok(Math.abs(start - 10.8775) < 0.0001);
  assert.ok(Math.abs(end - 11.2052) < 0.0001);
  assert.ok(Math.abs(stroke - 11.0481) < 0.0001);
  assert.ok(Math.abs(end - start - 0.327693) < 0.0001);
  assert.ok(
    Math.abs(midpoint - percentOfDuration((423.56 + 436.32) / 2, duration)) <
      0.000001,
  );
});

test("consumer playback shortcut map keeps stroke navigation precedence", () => {
  const action = (code: string, key = "", shiftKey = false) =>
    playerShortcutAction({ code, key, shiftKey });
  assert.equal(action("Space"), "toggle");
  assert.equal(action("KeyK"), "toggle");
  assert.equal(action("KeyJ"), "seek-back-10");
  assert.equal(action("KeyL"), "seek-forward-10");
  assert.equal(action("ArrowLeft"), "seek-back-5");
  assert.equal(action("KeyM"), "toggle-mute");
  assert.equal(action("KeyF"), "toggle-fullscreen");
  assert.equal(action("Comma", ","), null);
  assert.equal(action("Period", "."), null);
  assert.equal(action("Comma", "<", true), "rate-down");
  assert.equal(action("Period", ">", true), "rate-up");
  assert.equal(action("ArrowRight", "", true), null);
});

test("held seek keys keep seeking while one-shot actions fire once", () => {
  assert.equal(repeatsWhileHeld("seek-back-5"), true);
  assert.equal(repeatsWhileHeld("seek-forward-5"), true);
  assert.equal(repeatsWhileHeld("seek-back-10"), true);
  assert.equal(repeatsWhileHeld("seek-forward-10"), true);
  assert.equal(repeatsWhileHeld("toggle"), false);
  assert.equal(repeatsWhileHeld("toggle-mute"), false);
  assert.equal(repeatsWhileHeld("rate-up"), false);
});

test("inspection remains selected while video time advances active context", () => {
  const first = rally(0, 10, 12);
  const stroke = {
    eventIndex: 4,
    strokeIndex: 4,
    frame: 275,
    time: 11,
    ordinal: 1,
    player: "選手 A",
    type: null,
    confidence: null,
  };
  first.hits = [stroke];
  const second = rally(1, 20, 22);
  const model: MatchModel = {
    title: "Variable match",
    video: "/video",
    duration: 30,
    scenario: "test",
    players: { a: "選手 A", b: "選手 B" },
    states: { segments: { status: "available" } },
    capabilities: capabilityFromStates({ segments: { status: "available" } }),
    rallies: [first, second],
    commentaryAvailability: {
      coverage: "none",
      availableRallyCount: 0,
      unsupportedRallyCount: 0,
      totalRallyCount: 2,
    },
  };
  const seeks: number[] = [];
  const workspace = useReviewWorkspace(
    ref<MatchModel | null>(model),
    ref({ seek: (time: number) => seeks.push(time) }),
  );
  workspace.selectRally(first);
  workspace.updateTime(21);
  assert.equal(workspace.selectedRally.value?.id, 0);
  assert.equal(workspace.activeRally.value?.id, 1);
  workspace.selectStroke(stroke);
  assert.deepEqual(seeks, [10, 11]);
  assert.equal(workspace.selectedStroke.value?.eventIndex, 4);
});

test("model replacement resets same-scenario selection, time, and player seek", async () => {
  const firstRaw = rawMatch([rally(0, 1, 2)]);
  const first: MatchModel = {
    ...firstRaw,
    capabilities: capabilityFromStates(firstRaw.states),
  };
  const second: MatchModel = {
    ...rawMatch([rally(9, 4, 6)]),
    scenario: first.scenario,
    video: first.video,
    capabilities: capabilityFromStates({
      segments: { status: "available" },
    }),
  };
  const model = ref<MatchModel | null>(first);
  const seeks: number[] = [];
  const workspace = useReviewWorkspace(
    model,
    ref({ seek: (time: number) => seeks.push(time) }),
  );
  workspace.selectRally(first.rallies[0]);
  workspace.updateTime(1.5);
  model.value = second;
  await nextTick();
  assert.equal(workspace.currentTimeSec.value, 0);
  assert.equal(workspace.selectedRally.value, null);
  assert.equal(workspace.selectedStroke.value, null);
  assert.deepEqual(seeks, [1, 0]);
});

test("same-rally stroke stepping seeks exact events and stops at boundaries", () => {
  const selected = rally(0, 10, 14);
  selected.hits = [10.5, 11.5, 12.5].map((time, index) => ({
    eventIndex: index,
    strokeIndex: index,
    frame: 100 + index,
    time,
    ordinal: index + 1,
    player: "選手 A",
    type: null,
    confidence: null,
  }));
  const model: MatchModel = {
    title: "Step match",
    video: "/video",
    duration: 20,
    scenario: "step",
    players: { a: "選手 A", b: "選手 B" },
    states: { segments: { status: "available" } },
    capabilities: capabilityFromStates({ segments: { status: "available" } }),
    rallies: [selected, rally(1, 16, 18)],
    commentaryAvailability: {
      coverage: "none",
      availableRallyCount: 0,
      unsupportedRallyCount: 0,
      totalRallyCount: 2,
    },
  };
  const seeks: number[] = [];
  const workspace = useReviewWorkspace(
    ref<MatchModel | null>(model),
    ref({ seek: (time: number) => seeks.push(time) }),
  );
  workspace.selectRallyAt(selected, 11);
  workspace.updateTime(11);
  assert.equal(workspace.moveStroke(1), true);
  assert.equal(workspace.selectedStroke.value?.time, 11.5);
  assert.equal(workspace.moveStroke(1), true);
  assert.equal(workspace.moveStroke(1), false);
  assert.equal(workspace.moveStroke(-1), true);
  assert.deepEqual(seeks, [11, 11.5, 12.5, 11.5]);
});

test("commentary navigation reuses canonical Rally and Stroke selection", () => {
  const selected = rally(0, 10, 12);
  const stroke = {
    eventIndex: 7,
    strokeIndex: 7,
    frame: 275,
    time: 11,
    ordinal: 1,
    player: "選手 A",
    type: "殺球",
    confidence: 0.9,
  };
  selected.hits = [stroke];
  const model: MatchModel = {
    ...rawMatch([selected]),
    capabilities: capabilityFromStates({}, true),
  };
  const seeks: number[] = [];
  const workspace = useReviewWorkspace(
    ref<MatchModel | null>(model),
    ref({ seek: (time: number) => seeks.push(time) }),
  );
  workspace.selectCommentary(
    {
      segmentIndex: 0,
      strokeIndex: 7,
      frame: 275,
      timeSec: 11,
      player: "a",
      text: "精確擊球賽評",
      sourceFactIds: ["rally:0:stroke:7"],
      evidence: [],
    },
    selected,
  );
  assert.equal(workspace.selectedRally.value?.id, 0);
  assert.equal(workspace.selectedStroke.value?.eventIndex, 7);
  assert.deepEqual(seeks, [11]);
  assert.equal("selectedCommentaryEventIndex" in workspace, false);
});

test("runtime model validation rejects malformed JSON and normalizes legacy cached cheer data", () => {
  assert.throws(() => parseMatchModel({ title: "incomplete" }));
  const parsed = parseMatchModel({
    title: "Legacy cache",
    video: "/video",
    duration: 10,
    scenario: "legacy",
    players: { a: "A", b: "B" },
    states: {
      segments: { status: "available" },
      audio_signals: { status: "available" },
    },
    rallies: [
      {
        ...rally(0, 1, 2),
        audio: {
          segment_index: 0,
          cheer_confidence: 0,
          cheer_intensity: null,
          n_cheer_windows: 0,
        },
      },
    ],
  });
  assert.deepEqual(parsed.rallies[0].audio, {
    segmentIndex: 0,
    confidence: 0,
    intensity: null,
    windowCount: 0,
  });
  assert.equal(parsed.capabilities.cheer, true);
});

test("runtime model validation preserves canonical cached cheer data", () => {
  const parsed = parseMatchModel({
    title: "Canonical cache",
    video: "/video",
    duration: 10,
    scenario: "canonical",
    players: { a: "A", b: "B" },
    states: {
      segments: { status: "available" },
      audio_signals: { status: "available" },
    },
    rallies: [
      {
        ...rally(0, 1, 2),
        audio: {
          segmentIndex: 0,
          confidence: 0.75,
          intensity: 0.5,
          windowCount: 3,
        },
      },
    ],
  });
  assert.deepEqual(parsed.rallies[0].audio, {
    segmentIndex: 0,
    confidence: 0.75,
    intensity: 0.5,
    windowCount: 3,
  });
});

test("precise time rounds before minute extraction and never emits .100", () => {
  assert.equal(formatPreciseTime(59.994), "00:59.99");
  assert.equal(formatPreciseTime(59.995), "01:00.00");
  assert.equal(formatPreciseTime(60), "01:00.00");
  assert.equal(formatPreciseTime(-1), "00:00.00");
  assert.deepEqual(formatPreciseTimeParts(59.995), {
    whole: "01:00",
    fraction: "00",
    text: "01:00.00",
  });
  assert.doesNotMatch(formatPreciseTime(1.999), /\.100$/);
});

test("score text and short local axes use evidence-appropriate precision", () => {
  assert.equal(scoreText([9, 14]), "9:14");
  assert.equal(formatTimelineAxisTime(1.25, 2), "00:01.25");
  assert.equal(formatTimelineAxisTime(61.25, 120), "01:01");
});

test("edge labels align inward without changing temporal registration", () => {
  const midpoint = percentOfDuration(5, 100);
  assert.equal(midpoint, 5);
  assert.equal(edgeLabelSide(5, 200, 18), "start");
  assert.equal(edgeLabelSide(100, 200, 18), "center");
  assert.equal(edgeLabelSide(195, 200, 18), "end");
  assert.equal(midpoint, 5);
});

test("runtime parser rejects impossible canonical timeline states", () => {
  const invalidBounds = rally(0, 3, 2);
  assert.throws(() => parseMatchModel(rawMatch([invalidBounds])), /Rally end/);

  const beyondDuration = rally(0, 9, 11);
  assert.throws(
    () => parseMatchModel(rawMatch([beyondDuration])),
    /match duration/,
  );

  assert.throws(
    () => parseMatchModel(rawMatch([rally(0, 1, 2), rally(0, 3, 4)])),
    /unique/,
  );

  const invalidScore = { ...rally(0, 1, 2), score: [1.5, -1] };
  assert.throws(() =>
    parseMatchModel({ ...rawMatch(), rallies: [invalidScore] }),
  );

  const outsideStroke = rally(0, 1, 2);
  outsideStroke.hits = [
    {
      eventIndex: 7,
      strokeIndex: 0,
      frame: 0,
      time: 3,
      ordinal: 1,
      player: "A",
      type: null,
      confidence: null,
    },
  ];
  assert.throws(
    () => parseMatchModel(rawMatch([outsideStroke])),
    /within its Rally/,
  );

  const duplicateStrokeA = rally(0, 1, 2);
  duplicateStrokeA.hits = [{ ...outsideStroke.hits[0], time: 1.5 }];
  const duplicateStrokeB = rally(1, 3, 4);
  duplicateStrokeB.hits = [{ ...outsideStroke.hits[0], time: 3.5 }];
  assert.throws(
    () => parseMatchModel(rawMatch([duplicateStrokeA, duplicateStrokeB])),
    /globally unique/,
  );

  const audioMismatch = rally(0, 1, 2);
  audioMismatch.audio = {
    segmentIndex: 2,
    confidence: 0,
    intensity: null,
    windowCount: 0,
  };
  assert.throws(
    () => parseMatchModel(rawMatch([audioMismatch])),
    /Audio segment/,
  );

  const commentaryMismatch = rally(0, 1, 2);
  commentaryMismatch.hits = [
    {
      eventIndex: 7,
      strokeIndex: 7,
      frame: 30,
      time: 1.5,
      ordinal: 1,
      player: "A",
      type: null,
      confidence: null,
    },
  ];
  commentaryMismatch.commentary = {
    status: "available",
    source: "on-demand",
    summary: null,
    events: [
      {
        segmentIndex: 0,
        strokeIndex: 7,
        frame: 31,
        timeSec: 1.5,
        player: "a",
        text: "mismatch",
        sourceFactIds: ["rally:0:stroke:7"],
        evidence: [],
      },
    ],
  };
  assert.throws(
    () => parseMatchModel(rawMatch([commentaryMismatch])),
    /canonical Stroke/,
  );
});

test("repository and presentation components do not traverse backend artifact field names", () => {
  const componentFiles = readdirSync("src/components", { recursive: true })
    .filter(
      (file): file is string =>
        typeof file === "string" && file.endsWith(".vue"),
    )
    .map((file) => `src/components/${file.replaceAll("\\", "/")}`);
  const files = [
    "src/data/matchRepository.ts",
    "src/App.vue",
    "src/layouts/MatchShell.vue",
    "src/pages/MatchesPage.vue",
    "src/pages/ReviewPage.vue",
    ...componentFiles,
  ];
  const presentation = files
    .map((file) => readFileSync(file, "utf8"))
    .join("\n");
  assert.doesNotMatch(
    presentation,
    /\b(score_a|score_b|segment_index|stroke_index|source_fact_ids|cheer_confidence|cheer_intensity|n_cheer_windows)\b/,
  );
});

test("production import delegates artifact semantics and court policy to Python", () => {
  const importer = readFileSync("scripts/local-matches.ts", "utf8");
  assert.match(importer, /execFileSync\([\s\S]*modules\.review_export/);
  assert.doesNotMatch(importer, /deriveCourtPositions|segment_index|cheer_confidence|source_fact_ids/);
  assert.doesNotMatch(importer, /status\.inputs|fingerprint\(/);
  assert.equal(
    readdirSync("scripts").includes("court-positions.ts"),
    false,
  );
});
