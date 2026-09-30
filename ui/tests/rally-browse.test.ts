import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import type { MatchModel, RallyModel } from "../src/domain/models";
import {
  browseRallies,
  normalizeRallyBrowseQuery,
  rallyGames,
} from "../src/rallies/rallyBrowse";
import {
  canonicalInteger,
  findStrokeTarget,
  resolveRouteRally,
  resolveRouteStroke,
} from "../src/rallies/rallyRoute";

function rally(
  id: number,
  game: number | null,
  highlight: number | null,
  commentary: RallyModel["commentary"],
  eventIndex: number,
): RallyModel {
  return {
    id,
    game,
    highlight,
    commentary,
    start: id * 10,
    end: id * 10 + 5,
    duration: 5,
    score: null,
    multi: false,
    subScores: [],
    splits: [],
    audio: null,
    hits: [
      {
        eventIndex,
        strokeIndex: eventIndex,
        frame: eventIndex,
        time: id * 10 + 1,
        ordinal: 1,
        player: "a",
        type: null,
        confidence: null,
      },
    ],
  };
}
const unavailable: RallyModel["commentary"] = {
  status: "unavailable",
  source: null,
  summary: null,
  events: [],
};
const available: RallyModel["commentary"] = {
  status: "available",
  source: "on-demand",
  summary: null,
  events: [],
};
const model: MatchModel = {
  players: { a: "A", b: "B" },
  title: "Test",
  video: "test.mp4",
  duration: 100,
  scenario: "test",
  capabilities: {
    score: false,
    stroke: true,
    identity: false,
    cheer: false,
    highlight: true,
    commentary: true,
    court: false,
    pose: false,
    shuttle: false,
  },
  states: {},
  commentaryAvailability: {
    coverage: "partial",
    availableRallyCount: 1,
    unsupportedRallyCount: 0,
    totalRallyCount: 3,
  },
  rallies: [
    rally(10, 0, 0.2, unavailable, 100),
    rally(2, 1, 0.9, available, 671),
    rally(42, 1, 0.9, unavailable, 900),
  ],
};

test("browse query accepts only canonical game, commentary and available Highlight sort", () => {
  assert.deepEqual(rallyGames(model), [0, 1]);
  const normalized = normalizeRallyBrowseQuery(
    { game: "1", commentary: "available", sort: "highlight", junk: "x" },
    model,
  );
  assert.deepEqual(normalized.state, {
    game: 1,
    commentary: "available",
    sort: "highlight",
  });
  assert.deepEqual(normalized.query, {
    game: "1",
    commentary: "available",
    sort: "highlight",
  });
  const invalid = normalizeRallyBrowseQuery(
    { game: "7", commentary: "maybe", sort: "cheer" },
    model,
  );
  assert.deepEqual(invalid.query, {});
});

test("browse filtering preserves partial commentary truth and canonical IDs", () => {
  const filtered = browseRallies(model, {
    game: null,
    commentary: "available",
    sort: "time",
  });
  assert.deepEqual(
    filtered.map((item) => item.id),
    [2],
  );
  assert.equal(
    filtered.length,
    model.commentaryAvailability.availableRallyCount,
  );
});

test("Highlight ranking never uses Cheer and falls back when capability is missing", () => {
  assert.deepEqual(
    browseRallies(model, {
      game: null,
      commentary: "all",
      sort: "highlight",
    }).map((item) => item.id),
    [2, 42, 10],
  );
  const without = {
    ...model,
    capabilities: { ...model.capabilities, highlight: false },
  };
  assert.equal(
    normalizeRallyBrowseQuery({ sort: "highlight" }, without).state.sort,
    "time",
  );
});

test("route identity uses canonical Rally.id and Stroke.eventIndex without nearest fallback", () => {
  assert.equal(resolveRouteRally(model, "42")?.id, 42);
  assert.equal(resolveRouteRally(model, "0"), null);
  assert.equal(resolveRouteRally(model, "02"), null);
  const owner = resolveRouteRally(model, "2")!;
  assert.equal(resolveRouteStroke(owner, "671")?.eventIndex, 671);
  assert.equal(resolveRouteStroke(owner, "1"), null);
  assert.equal(resolveRouteStroke(owner, "900"), null);
  assert.equal(findStrokeTarget(model, 900)?.rally.id, 42);
  assert.equal(canonicalInteger("-1"), null);
});

test("Rally Detail keeps one fixed local ReviewTimeline and URL replacement paths", () => {
  const page = readFileSync("src/pages/RallyDetailPage.vue", "utf8");
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  assert.match(page, /mode="rally-detail"/);
  assert.match(
    page,
    /router\.replace\(detailLocation\(rally\.value!, stroke\)\)/,
  );
  assert.match(
    page,
    /router\.push\(detailLocation\(target\.rally, target\.stroke\)\)/,
  );
  assert.match(page, /workspace\.selectRally\(target\)/);
  assert.match(page, /workspace\.selectStroke\(stroke\)/);
  assert.match(timeline, /mode === "rally-detail"/);
  assert.match(timeline, /v-if="mode === 'review'" class="fit-controls"/);
  assert.doesNotMatch(page, /selectedCommentaryEvent/);
});

test("Review exposes one route to Rally Detail and no competing embedded browser", () => {
  const inspector = readFileSync(
    "src/components/inspector/RallyInspector.vue",
    "utf8",
  );
  const review = readFileSync("src/pages/ReviewPage.vue", "utf8");
  assert.doesNotMatch(inspector, /RallyBrowser/);
  assert.match(inspector, />\s*開啟片段\s*<\/button>/);
  assert.match(
    review,
    /query: stroke \? \{ stroke: String\(stroke\.eventIndex\) \} : \{\}/,
  );
});
