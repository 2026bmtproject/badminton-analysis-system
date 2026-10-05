import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { ref } from "vue";
import type {
  CommentaryEventModel,
  MatchCapabilities,
  MatchModel,
  RallyModel,
  StrokeModel,
} from "../src/domain/models";
import { useReviewWorkspace } from "../src/state/useReviewWorkspace";
import { defaultWorkspaceLayout } from "../src/state/workspaceLayout";
import {
  activeRallyAt,
  resolveActiveMatchContext,
} from "../src/temporal/activeContext";
import {
  playbackFollowViewport,
  viewportContainsTime,
} from "../src/temporal/timelineFollow";

const capabilities: MatchCapabilities = {
  score: true,
  stroke: true,
  identity: false,
  cheer: true,
  highlight: true,
  commentary: true,
  court: true,
  pose: false,
  shuttle: false,
};

function stroke(eventIndex: number, time: number): StrokeModel {
  return {
    eventIndex,
    strokeIndex: eventIndex,
    frame: Math.round(time * 30),
    time,
    ordinal: eventIndex + 1,
    player: eventIndex % 2 ? "B" : "A",
    type: "smash",
    confidence: 0.9,
    courtPosition: {
      x: 0.4,
      y: 0.6,
      coordinateSpace: "court_normalized_v1",
      source: "ankle_midpoint",
      sourceFrame: Math.round(time * 30),
    },
  };
}

function rally(
  id: number,
  start: number,
  end: number,
  score: readonly [number, number],
  hits: StrokeModel[],
): RallyModel {
  return {
    id,
    start,
    end,
    duration: end - start,
    score,
    game: 1,
    multi: false,
    subScores: [],
    splits: [],
    hits,
    audio: { segmentIndex: id, confidence: 0.8, intensity: 0.5, windowCount: 2 },
    highlight: 0.65,
    commentary: {
      status: "available",
      source: "full-match",
      summary: null,
      events: [],
    },
  };
}

const firstStroke = stroke(10, 10.5);
const secondStroke = stroke(11, 11.5);
const thirdStroke = stroke(20, 14.5);
const firstRally = rally(0, 10, 12, [1, 0], [firstStroke, secondStroke]);
const secondRally = rally(1, 14, 17, [1, 1], [thirdStroke]);
const adjacentRally = rally(2, 12, 14, [1, 1], []);

const model: MatchModel = {
  players: { a: "A", b: "B" },
  title: "Playback follow fixture",
  video: "/fixture.mp4",
  duration: 30,
  scenario: "test",
  capabilities,
  states: {
    scores: { status: "available" },
    events: { status: "available" },
    audio_signals: { status: "available" },
    highlights: { status: "available" },
    commentary: { status: "available" },
    court: { status: "available" },
  },
  rallies: [firstRally, secondRally],
  commentaryAvailability: {
    coverage: "complete",
    availableRallyCount: 2,
    unsupportedRallyCount: 0,
    totalRallyCount: 2,
  },
};

function workspaceHarness() {
  const seeks: number[] = [];
  const workspace = useReviewWorkspace(
    ref<MatchModel | null>(model),
    ref({ seek: (timeSec: number) => seeks.push(timeSec) }),
  );
  return { workspace, seeks };
}

test("1. normal playback follows rally and stroke transitions automatically", () => {
  const { workspace } = workspaceHarness();
  workspace.updateTime(10.75);
  assert.equal(workspace.activeRally.value?.id, 0);
  assert.equal(workspace.activeStroke.value?.eventIndex, 10);
  workspace.updateTime(11.75);
  assert.equal(workspace.activeStroke.value?.eventIndex, 11);
  workspace.updateTime(14.75);
  assert.equal(workspace.activeRally.value?.id, 1);
  assert.equal(workspace.activeStroke.value?.eventIndex, 20);
});

test("2. playback leaves an explicitly selected old stroke behind without stale context", () => {
  const { workspace, seeks } = workspaceHarness();
  workspace.selectStroke(firstStroke);
  assert.deepEqual(seeks, [10.5]);
  assert.equal(workspace.selectedRallyIndex.value, 0);
  workspace.updateTime(14.75);
  assert.equal(workspace.selectedRallyIndex.value, 0, "selection remains an explicit reference");
  assert.equal(workspace.activeRally.value?.id, 1, "playback context is authoritative");
  assert.equal(workspace.activeStroke.value?.eventIndex, 20);
});

test("3. half-open intervals assign an exact shared boundary to the new rally", () => {
  assert.equal(activeRallyAt([firstRally, adjacentRally], 11.999)?.id, 0);
  assert.equal(activeRallyAt([firstRally, adjacentRally], 12)?.id, 2);
});

test("4. real gaps clear rally, stroke, court and segment score observations", () => {
  const context = resolveActiveMatchContext(model, 13);
  assert.equal(context.rally, null);
  assert.equal(context.stroke, null);
  assert.equal(context.scoreRally, null);
  assert.equal(context.score, null);
});

test("5. all active consumers are wired to the same authoritative context", () => {
  const page = readFileSync("src/pages/ReviewPage.vue", "utf8");
  assert.match(page, /:active-stroke="workspace\.activeStroke\.value"/);
  assert.match(page, /@time="workspace\.updateTime"/);
  assert.match(page, /:active-id="activeId"/);
  assert.match(page, /:active-stroke-index="workspace\.activeStroke\.value\?\.eventIndex \?\? null"/);
  assert.match(page, /:current-score="workspace\.currentScore\.value"/);
  assert.match(page, /:score-context-id="workspace\.activeScoreRally\.value\?\.id \?\? null"/);
});

test("6. court stroke selection seeks once and playback context resolves that stroke", () => {
  const { workspace, seeks } = workspaceHarness();
  workspace.selectStroke(secondStroke);
  workspace.updateTime(seeks.at(-1)!);
  assert.equal(workspace.activeRally.value?.id, 0);
  assert.equal(workspace.activeStroke.value?.eventIndex, 11);
  const analysis = readFileSync("src/components/workspace/AnalysisWindow.vue", "utf8");
  assert.match(analysis, /:selected-stroke-index="activeStrokeIndex"/);
});

test("7. commentary selection seeks to its linked stroke and resolves shared context", () => {
  const { workspace, seeks } = workspaceHarness();
  const comment: CommentaryEventModel = {
    segmentIndex: 1,
    text: "攻防轉換",
    sourceFactIds: [],
    evidence: [],
    strokeIndex: 20,
    frame: 435,
    timeSec: 14.5,
    player: "a",
  };
  workspace.selectCommentary(comment, secondRally);
  workspace.updateTime(seeks.at(-1)!);
  assert.equal(workspace.activeRally.value?.id, 1);
  assert.equal(workspace.activeStroke.value?.eventIndex, 20);
});

test("8. persisted workspace geometry excludes transient playback and selection state", () => {
  const persisted = JSON.stringify(defaultWorkspaceLayout());
  assert.doesNotMatch(persisted, /selected|activeRally|activeStroke|currentTime/i);
  const { workspace } = workspaceHarness();
  assert.equal(workspace.selectedRallyIndex.value, null);
  assert.equal(workspace.selectedStrokeIndex.value, null);
});

test("9. manual timeline navigation suspends follow until return-to-playback", () => {
  const oldView = { startSec: 10, endSec: 12, durationSec: 2 };
  assert.equal(playbackFollowViewport(true, 14.5, oldView, 30, secondRally), oldView);
  const followed = playbackFollowViewport(false, 14.5, oldView, 30, secondRally);
  assert.notEqual(followed, oldView);
  assert.equal(viewportContainsTime(followed, 14.5), true);
});

test("10. responsive layout changes presentation only, not temporal ownership", () => {
  const styles = readFileSync("src/styles/floating-workspace.css", "utf8");
  const page = readFileSync("src/pages/ReviewPage.vue", "utf8");
  assert.match(styles, /@media \(max-width: 1100px\)/);
  assert.match(styles, /\.workspace-window\s*\{[\s\S]*position:\s*relative/s);
  assert.equal((page.match(/workspace\.activeStroke\.value/g) ?? []).length >= 2, true);
  assert.doesNotMatch(styles, /activeRally|selectedRally|currentTime/);
});
