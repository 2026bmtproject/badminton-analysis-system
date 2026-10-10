import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { adapt, manifestSchema, type Input } from "../scripts/adapter";
const manifest = manifestSchema.parse(
  JSON.parse(readFileSync("fixtures/manifest.json", "utf8")),
);
function fixtures(): Input {
  return Object.fromEntries(
    manifest.scenarios[0].stages.map((s) => [
      s,
      JSON.parse(readFileSync(`fixtures/stages/${s}.json`, "utf8")),
    ]),
  );
}
function full() {
  return adapt(fixtures(), manifest, "full");
}
test("absolute time, full-match indexes and segment mapping preserve IDs", () => {
  const m = full();
  const h = m.rallies[1].hits![2];
  assert.equal(h.eventIndex, 7);
  assert.equal(h.strokeIndex, 7);
  assert.equal(h.ordinal, 3);
  assert.equal(h.time, 14.48);
  assert.equal(h.frame, 362);
  assert.equal(h.player, "球員 A");
  assert.deepEqual(
    m.rallies.map((r) => r.id),
    [0, 1, 2, 3],
  );
});
test("no implicit top/bottom identity; explicit side change and unknown/zero survive", () => {
  const m = full();
  assert.equal(m.rallies[2].hits![0].player, "畫面上方");
  assert.equal(m.rallies[2].hits![1].player, "畫面下方");
  assert.equal(m.rallies[2].hits![2].player, "未知球員");
  assert.equal(m.rallies[2].hits![2].confidence, 0);
  assert.equal(m.rallies[3].hits![0].player, "球員 B");
  assert.equal(m.rallies[3].audio!.intensity, 0);
  assert.equal(m.rallies[0].audio!.intensity, null);
});
test("missing optional stages do not become zero, failure, or invented strokes", () => {
  const raw = fixtures();
  for (const s of [
    "audio_signals",
    "highlights",
    "strokes",
    "commentary",
    "commentary_segments",
  ])
    delete raw[s];
  const m = adapt(raw, manifest, "minimal");
  assert.equal(m.states.strokes.status, "missing");
  assert.equal(m.rallies[1].hits![2].type, null);
  assert.equal(m.rallies[1].hits![2].eventIndex, 7);
  assert.equal(m.rallies[1].highlight, null);
  assert.equal(m.rallies[1].commentary.status, "unavailable");
  delete raw.events;
  assert.equal(adapt(raw, manifest, "none").rallies[0].hits, null);
  raw.events = { events: [] };
  assert.deepEqual(adapt(raw, manifest, "empty").rallies[0].hits, []);
});
test("score is the segment scoreboard observation and never derives a transition", () => {
  const m = full();
  assert.deepEqual(m.rallies[0].score, [18, 17]);
  assert.deepEqual(m.rallies[1].score, [18, 18]);
  assert.deepEqual(m.rallies[2].score, [19, 19]);
  const raw = fixtures();
  const scores = raw.scores as {
    rallies: {
      segment_index: number;
      game_index: number | null;
      score_a: number;
    }[];
  };
  scores.rallies = scores.rallies.filter((r) => r.segment_index !== 1);
  assert.deepEqual(adapt(raw, manifest, "gap").rallies[2].score, [19, 19]);
  const invalid = fixtures();
  (invalid.scores as typeof scores).rallies[1].score_a = 21;
  assert.deepEqual(adapt(invalid, manifest, "jump").rallies[1].score, [21, 18]);
  const unknown = fixtures();
  (unknown.scores as typeof scores).rallies[1].game_index = null;
  assert.deepEqual(
    adapt(unknown, manifest, "unknown-game").rallies[1].score,
    [18, 18],
  );
});
test("sub_scores remain one source segment; split_secs remain scoreboard observations", () => {
  const m = full();
  assert.equal(m.rallies.length, 4);
  assert.equal(m.rallies[2].id, 2);
  assert.equal(m.rallies[2].multi, true);
  assert.deepEqual(m.rallies[2].subScores, [
    [19, 18],
    [19, 19],
  ]);
  assert.deepEqual(m.rallies[2].splits, [28]);
  assert.equal(m.rallies[2].start, 22);
  assert.equal(m.rallies[2].end, 33);
});
test("exact source fact resolution, no nearest-time pairing for unavailable references", () => {
  const m = full();
  const e = m.rallies[1].commentary.events[0].evidence;
  assert.deepEqual(
    e.map((e) => e.eventIndex),
    [7, null],
  );
  assert.equal(e[0].time, 14.48);
  assert.match(e[0].text, /第 3 拍.*球員 A.*殺球/);
  const raw = fixtures();
  const c = raw.commentary_segments as {
    rally: { events: { source_fact_ids: string[] }[] };
  }[];
  c[1].rally.events[0].source_fact_ids = [
    "rally:0:stroke:7",
    "rally:1:stroke:999",
  ];
  const model = adapt(raw, manifest, "bad-ref");
  assert.ok(
    model.rallies[1].commentary.events[0].evidence.every(
      (e) => e.time === null,
    ),
  );
});
test("malformed optional input reports error while available events stay usable", () => {
  const raw = fixtures();
  raw.strokes = { strokes: [{ event_index: 0 }] };
  raw.audio_signals = { readError: true };
  const m = adapt(raw, manifest, "failed");
  assert.equal(m.states.strokes.status, "error");
  assert.equal(m.states.audio_signals.status, "error");
  assert.equal(m.rallies[0].hits!.length, 5);
  assert.equal(m.rallies[0].hits![0].type, null);
});
test("mismatched source timing fails closed at the affected stage", () => {
  const raw = fixtures();
  const s = raw.strokes as { strokes: { frame: number }[] };
  s.strokes[7].frame++;
  assert.equal(
    adapt(raw, manifest, "bad-stroke").states.strokes.status,
    "error",
  );
  const c = raw.commentary as { rallies: { events: { time_sec: number }[] }[] };
  c.rallies[0].events[0].time_sec = 12;
  assert.equal(
    adapt(raw, manifest, "bad-comment").states.commentary.status,
    "error",
  );
  const sg = raw.segments as { segments: { start_sec: number }[] };
  sg.segments[0].start_sec = 0;
  assert.throws(() => adapt(raw, manifest, "bad-segment"), /片段影格與時間/);
});

test("production full and on-demand commentary merge atomically by segment", () => {
  const model = full();
  assert.equal(model.states.commentary.status, "available");
  assert.equal(model.states.commentary_segments.status, "available");
  assert.equal(model.rallies[0].commentary.source, "on-demand");
  assert.equal(model.rallies[0].commentary.summary, null);
  assert.equal(model.rallies[1].commentary.source, "on-demand");
  assert.match(model.rallies[1].commentary.events[0].text, /隨選更新/);
  assert.equal(model.rallies[1].commentary.events[0].strokeIndex, 7);
  assert.equal(model.rallies[1].commentary.events[0].timeSec, 14.48);
  assert.equal(model.rallies[1].commentary.events[0].player, "a");
  assert.ok(
    model.rallies[1].commentary.events[0].sourceFactIds.includes(
      "rally:1:stroke:7:pose",
    ),
  );
  assert.equal(model.rallies[2].commentary.status, "unavailable");
  assert.equal(model.rallies[3].commentary.status, "unsupported");
  assert.deepEqual(model.commentaryAvailability, {
    coverage: "partial",
    availableRallyCount: 2,
    unsupportedRallyCount: 1,
    totalRallyCount: 4,
  });
});

test("partial on-demand commentary works without canonical stage status", () => {
  const raw = fixtures();
  delete raw.commentary;
  const model = adapt(raw, manifest, "selected-only");
  assert.equal(model.states.commentary.status, "missing");
  assert.equal(model.states.commentary_segments.status, "available");
  assert.equal(model.capabilities.commentary, true);
  assert.equal(model.commentaryAvailability.coverage, "partial");
  assert.equal(model.rallies[0].commentary.status, "available");
  assert.equal(model.rallies[2].commentary.status, "unavailable");
});

test("invalid production commentary fails closed without affecting navigation data", () => {
  const raw = fixtures();
  const selected = raw.commentary_segments as {
    segment_index: number;
    rally: {
      segment_index: number;
      events: { player?: string; segment_index: number }[];
    };
  }[];
  selected[0].rally.events[0].segment_index = 1;
  delete selected[1].rally.events[0].player;
  const model = adapt(raw, manifest, "invalid-selected");
  assert.equal(model.states.commentary_segments.status, "error");
  assert.equal(model.rallies[0].commentary.status, "unavailable");
  assert.equal(model.rallies[1].commentary.source, "full-match");
  assert.equal(model.rallies[1].hits![2].eventIndex, 7);
});

test("duplicate selected segment commentary is rejected before final merge", () => {
  const raw = fixtures();
  const selected = raw.commentary_segments as unknown[];
  selected.push(structuredClone(selected[0]));
  const model = adapt(raw, manifest, "duplicate-selected");
  assert.equal(model.states.commentary_segments.status, "error");
  assert.equal(model.rallies[0].commentary.status, "unavailable");
  assert.equal(
    model.rallies.filter((item) => item.commentary.source === "full-match")
      .length,
    1,
  );
});
