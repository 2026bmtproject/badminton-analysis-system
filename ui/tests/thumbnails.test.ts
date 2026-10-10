import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { desktopRuntime } from "../scripts/local-matches";
import { startLocalHost } from "../scripts/local-host";
import { mediaTool } from "../scripts/media-tools";
import { thumbnailTime } from "../scripts/thumbnails";

async function temporary(fn: (root: string) => Promise<void>) {
  const root = await mkdtemp(join(tmpdir(), "badminton-thumbnail-"));
  try { await fn(root); } finally { await rm(root, { recursive: true, force: true }); }
}

async function writeSegments(matchRoot: string, segments: unknown) {
  await mkdir(join(matchRoot, "stages", "match_segmentation"), { recursive: true });
  await writeFile(join(matchRoot, "stages", "match_segmentation", "segments.json"), JSON.stringify({ segments }));
}

test("the thumbnail time is the middle of the first segment", async () => {
  await temporary(async root => {
    assert.equal(await thumbnailTime(root), null);
    await writeSegments(root, []);
    assert.equal(await thumbnailTime(root), null);
    await writeSegments(root, [{ start_sec: 10, end_sec: 14 }, { start_sec: 20, end_sec: 30 }]);
    assert.equal(await thumbnailTime(root), 12);
  });
});

test("the host serves a cached JPEG thumbnail and 404s without segments", async () => {
  await temporary(async root => {
    const ui = join(root, "ui"), dist = join(ui, "dist"), matches = join(root, "matches"), data = join(root, "data");
    await mkdir(dist, { recursive: true });
    await writeFile(join(dist, "index.html"), "<html></html>");
    const match = join(matches, "Sample");
    await mkdir(join(match, "input"), { recursive: true });
    execFileSync(mediaTool("ffmpeg"), ["-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=10",
      "-pix_fmt", "yuv420p", join(match, "input", "match.mp4")]);
    const host = await startLocalHost(desktopRuntime(ui, matches, root, data, "uv"), dist, { pipelinePort: () => null });
    try {
      assert.equal((await fetch(`${host.origin}/local-thumbnail/Sample`)).status, 404);
      assert.equal((await fetch(`${host.origin}/local-thumbnail/..%2Fsecret`)).status, 404);
      await writeSegments(match, [{ start_sec: 0.5, end_sec: 1.5 }]);
      const response = await fetch(`${host.origin}/local-thumbnail/Sample`);
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("content-type"), "image/jpeg");
      const body = Buffer.from(await response.arrayBuffer());
      assert.deepEqual([...body.subarray(0, 2)], [0xff, 0xd8]);
      const etag = response.headers.get("etag")!;
      assert.equal((await fetch(`${host.origin}/local-thumbnail/Sample`, { headers: { "If-None-Match": etag } })).status, 304);
      await writeSegments(match, [{ start_sec: 1, end_sec: 2 }]);
      assert.notEqual((await fetch(`${host.origin}/local-thumbnail/Sample`)).headers.get("etag"), etag);
      assert.equal((await readdir(join(data, "thumbnails"))).length, 1);
    } finally { await host.close(); }
  });
});
