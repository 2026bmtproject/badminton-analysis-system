import assert from "node:assert/strict";
import test from "node:test";
import { clientToCourt } from "../src/data/courtCalibration";

const image = { x: 0, y: 0, width: 2000, height: 1000 };

test("pointer coordinates follow the same image viewBox through resize", () => {
  const full = { left: 50, top: 20, width: 1000, height: 500 } as DOMRect;
  const half = { left: 50, top: 20, width: 500, height: 250 } as DOMRect;
  assert.deepEqual(clientToCourt(300, 145, full, image, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(175, 82.5, half, image, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(0, 0, full, image, 2000, 1000), [0, 0]);
  assert.deepEqual(clientToCourt(2000, 2000, full, image, 2000, 1000), [2000, 1000]);
});

test("padding around the image shifts the origin but never lets a corner leave the image", () => {
  const padded = { x: -100, y: -100, width: 2200, height: 1200 };
  const rect = { left: 0, top: 0, width: 1100, height: 600 } as DOMRect;
  assert.deepEqual(clientToCourt(50, 50, rect, padded, 2000, 1000), [0, 0]);
  assert.deepEqual(clientToCourt(300, 175, rect, padded, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(10, 10, rect, padded, 2000, 1000), [0, 0]);
});

test("a height-capped element letterboxes the viewBox instead of stretching it", () => {
  const capped = { left: 0, top: 0, width: 1000, height: 250 } as DOMRect;
  // Scale 0.25 leaves 250px empty on each side; the image spans x = 250..750.
  assert.deepEqual(clientToCourt(375, 62.5, capped, image, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(100, 0, capped, image, 2000, 1000), [0, 0]);
});
