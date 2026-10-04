import assert from "node:assert/strict";
import test from "node:test";
import { clientToCourt } from "../src/data/courtCalibration";

test("pointer coordinates follow the same image viewBox through resize", () => {
  const full = { left: 50, top: 20, width: 1000, height: 500 } as DOMRect;
  const half = { left: 50, top: 20, width: 500, height: 250 } as DOMRect;
  assert.deepEqual(clientToCourt(300, 145, full, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(175, 82.5, half, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(0, 0, full, 2000, 1000), [0, 0]);
  assert.deepEqual(clientToCourt(2000, 2000, full, 2000, 1000), [2000, 1000]);
});
