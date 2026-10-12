import { test } from "node:test";
import assert from "node:assert/strict";
import type { StrokeModel } from "../src/domain/models";
import {
  CatAppearance, crossedHits, NYAN_RAINBOW, OIIA, OIIA_GRACE, oiiaFrame, oiiaSongPosition, partying, partyLight, rainbowCells,
  replacesShuttle,
} from "../src/fun/funModes";

const hit = (frame: number, fps = 25) => ({ frame, time: frame / fps }) as StrokeModel;

test("the oiia cat spins while the song sings and stands in its silences", () => {
  const [first, second] = OIIA.audio.bursts;
  const [spinA, spinB] = OIIA.spins;
  const [standFirst, standLast] = OIIA.stand;
  assert.equal(oiiaFrame(first![0]), spinA![0]);
  assert.equal(oiiaFrame(first![1] - 1e-6), spinA![1]);
  assert.equal(oiiaFrame(second![0]), spinB![0]);
  const between = (first![1] + second![0]) / 2;
  assert.ok(oiiaFrame(between) >= standFirst && oiiaFrame(between) <= standLast);
  for (let t = 0; t < OIIA.audio.loopEnd; t += 0.01) {
    const frame = oiiaFrame(t);
    assert.ok(frame >= 0 && frame < OIIA.count, `frame ${frame} at ${t}`);
  }
});

test("each cat appearance starts the song just before its first voice and loops past the silence at the end", () => {
  assert.equal(oiiaSongPosition(0), OIIA.audio.loopStart);
  assert.ok(OIIA.audio.loopStart < OIIA.audio.bursts[0]![0]);
  const length = OIIA.audio.loopEnd - OIIA.audio.loopStart;
  assert.ok(Math.abs(oiiaSongPosition(length + 0.25) - (OIIA.audio.loopStart + 0.25)) < 1e-9);
});

test("a cat appearance outlasts short tracking gaps but not long ones, seeks back or resets", () => {
  const cat = new CatAppearance();
  assert.equal(cat.update(10, false), null);
  assert.equal(cat.update(10.04, true), 0);
  assert.ok(Math.abs(cat.update(10.3, false)! - 0.26) < 1e-9, "a short gap keeps the song going");
  assert.ok(Math.abs(cat.update(10.5, true)! - 0.46) < 1e-9);
  assert.equal(cat.update(10.5 + OIIA_GRACE + 0.1, false), null, "a long gap ends it");
  assert.equal(cat.update(12, true), 0, "the next appearance starts over");
  assert.equal(cat.update(11, true), 0, "playing backwards starts over");
  cat.reset();
  assert.equal(cat.update(11.04, false), null);
});

test("the party lights run from the first hit to just after the last, inside the Rally", () => {
  const rally = { end: 20, hits: [hit(250), hit(300)] };
  assert.equal(partying(rally, 9.9), false);
  assert.equal(partying(rally, 10), true);
  assert.equal(partying(rally, 12.9), true);
  assert.equal(partying(rally, 13.1), false);
  assert.equal(partying({ end: 12.5, hits: rally.hits }, 12.7), false);
  assert.equal(partying({ end: 20, hits: [] }, 10), false);
  assert.equal(partying(null, 10), false);
});

test("a party light splits into a 0-1 multiply colour and a brightness that peaks on the beat", () => {
  const onBeat = partyLight(0);
  const offBeat = partyLight(60 / 128 * 0.9);
  assert.ok(onBeat.brightness > offBeat.brightness);
  for (const light of [onBeat, offBeat]) {
    assert.ok(light.multiply.every((value) => value >= 0 && value <= 1));
    assert.equal(Math.max(...light.multiply), 1);
  }
});

test("the rainbow is whole cells, one set per band, and flips its wave with the tick", () => {
  const run = [[0, 100], [60, 100], [120, 100]] as const;
  const bands = rainbowCells(run, 3, 0);
  assert.equal(bands.length, NYAN_RAINBOW.length);
  assert.ok(bands.every((cells) => cells.length > 0 && cells.every(([x, y]) => Number.isInteger(x) && Number.isInteger(y))));
  // A level run stacks the bands one above another.
  const rows = bands.map((cells) => Math.min(...cells.map(([, y]) => y)));
  assert.deepEqual([...rows].sort((a, b) => a - b), rows);
  assert.notDeepEqual(rainbowCells(run, 3, 1), bands);
  assert.deepEqual(rainbowCells([[5, 5]], 3, 0), NYAN_RAINBOW.map(() => []));
});

test("the pipe only falls on hits played through, never on a seek", () => {
  const rallies = [{ hits: [hit(100), hit(140)] }, { hits: [hit(400)] }];
  assert.deepEqual(crossedHits(rallies, 99, 100, 12).map((item) => item.frame), [100]);
  assert.deepEqual(crossedHits(rallies, 100, 101, 12), [], "the hit already fell");
  assert.deepEqual(crossedHits(rallies, 395, 401, 12).map((item) => item.frame), [400]);
  assert.deepEqual(crossedHits(rallies, 90, 150, 12), [], "a jump is a seek");
  assert.deepEqual(crossedHits(rallies, 101, 99, 12), [], "backwards is a seek");
});

test("only the cats replace the shuttle", () => {
  assert.equal(replacesShuttle("oiia"), true);
  assert.equal(replacesShuttle("nyan"), true);
  assert.equal(replacesShuttle("pipe"), false);
  assert.equal(replacesShuttle("off"), false);
});
