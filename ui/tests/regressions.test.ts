import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  adapt,
  validateFixture,
  manifestSchema,
  type Input,
} from "../scripts/adapter";
import { createPlayback, type Media } from "../src/playback";
const manifest = manifestSchema.parse(
  JSON.parse(readFileSync("fixtures/manifest.json", "utf8")),
);
function inputs(): Input {
  return Object.fromEntries(
    manifest.scenarios[0].stages.map((s) => [
      s,
      JSON.parse(readFileSync(`fixtures/stages/${s}.json`, "utf8")),
    ]),
  );
}
type MutableMedia = { -readonly [K in keyof Media]: Media[K] };
function media(duration = 44, readyState = 4): MutableMedia {
  return {
    currentTime: 0,
    duration,
    readyState,
    paused: true,
    playbackRate: 1,
    pause() {
      this.paused = true;
    },
    async play() {
      this.paused = false;
    },
  };
}
test("one identity source reaches model, strokes and source observations", () => {
  const m = adapt(
    inputs(),
    { ...manifest, players: { a: "林選手", b: "陳選手" } },
    "renamed",
  );
  assert.deepEqual(m.players, { a: "林選手", b: "陳選手" });
  assert.equal(m.rallies[1].hits![2].player, m.players.a);
  assert.match(m.rallies[1].commentary.events[0].evidence[0].text, /林選手/);
  assert.equal(m.rallies[2].hits![0].player, "畫面上方");
});
test("optional errors remain reviewable; only fixture validation is strict", () => {
  const raw = inputs();
  raw.audio_signals = { readError: true };
  const m = adapt(raw, manifest, "failed-optional");
  assert.equal(m.states.audio_signals.status, "error");
  assert.equal(m.rallies[1].hits![2].time, 14.48);
  assert.deepEqual(m.rallies[1].score, [18, 18]);
  assert.throws(() => validateFixture(m), /Invalid declared fixture/);
});
test("summary citations do not become stroke commentary, explicit event/evidence do", () => {
  const events = adapt(inputs(), manifest, "full").rallies[1].commentary.events;
  const at = (strokeIndex: number) => events.filter((event) => event.strokeIndex === strokeIndex).length;
  assert.deepEqual([at(6), at(7), at(8), at(9)], [0, 1, 0, 0]);
});
test("source switch resets state, discards old seek and ignores late media events", async () => {
  const c = createPlayback();
  const old = media();
  c.attach(old);
  c.seek(18);
  c.setRate(2);
  await c.toggle();
  assert.equal(c.state.paused, false);
  c.reset();
  assert.deepEqual(c.state, {
    time: 0,
    duration: 0,
    paused: true,
    ready: false,
    error: "",
    rate: 1,
  });
  assert.equal(old.paused, true);
  c.seek(9);
  c.seek(12);
  const next = media(60, 0);
  c.attach(next);
  c.metadata(old);
  c.failed(old);
  assert.equal(c.state.ready, false);
  assert.equal(c.state.error, "");
  next.readyState = 4;
  c.metadata(next);
  assert.equal(next.currentTime, 12);
  assert.equal(c.state.time, 12);
  assert.equal(c.state.duration, 60);
  assert.equal(next.playbackRate, 1);
  c.seek(70);
  assert.equal(next.currentTime, 60);
  assert.equal(next.paused, true);
  await c.toggle();
  c.seek(20);
  assert.equal(next.paused, false);
  assert.equal(c.state.time, 20);
  c.pause();
  assert.equal(next.paused, true);
  assert.equal(c.state.paused, true);
  c.reset();
  c.seek(33);
  c.reset();
  const third = media(15);
  c.attach(third);
  assert.equal(third.currentTime, 0);
  assert.equal(c.state.time, 0);
});
test("late rejected play cannot contaminate new source; errors clear on reset", async () => {
  const c = createPlayback();
  const old = media();
  let reject: (reason?: unknown) => void = () => {};
  old.play = () =>
    new Promise((_, r) => {
      reject = r;
    });
  c.attach(old);
  const pending = c.toggle();
  c.reset();
  const next = media();
  c.attach(next);
  reject(new Error("old network"));
  await pending;
  assert.equal(c.state.error, "");
  c.failed(next);
  assert.match(c.state.error, /讀取失敗/);
  c.reset();
  assert.equal(c.state.error, "");
  assert.equal(c.state.ready, false);
});

test("hit labels distinguish stage failure, absence and actual zero", async () => {
  const { hitStatus } = await import("../src/review");
  assert.equal(hitStatus("error", null), "擊球資料讀取失敗");
  assert.equal(hitStatus("missing", null), "未提供擊球資料");
  assert.equal(hitStatus("available", 0), "未偵測到擊球");
});
