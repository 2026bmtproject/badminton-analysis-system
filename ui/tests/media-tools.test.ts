import { test } from "node:test";
import assert from "node:assert/strict";
import { isAbsolute } from "node:path";
import { mediaTool } from "../scripts/media-tools";

test("media tools use project-local portable binaries by default", () => {
  assert.ok(isAbsolute(mediaTool("ffmpeg")));
  assert.ok(isAbsolute(mediaTool("ffprobe")));
});

test("media tool environment overrides take precedence", () => {
  const previous = process.env.FFPROBE_BINARY;
  try {
    process.env.FFPROBE_BINARY = "configured-ffprobe";
    assert.equal(mediaTool("ffprobe"), "configured-ffprobe");
  } finally {
    if (previous === undefined) delete process.env.FFPROBE_BINARY;
    else process.env.FFPROBE_BINARY = previous;
  }
});
