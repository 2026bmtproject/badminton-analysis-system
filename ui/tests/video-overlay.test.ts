import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { capabilityFromStates, type RallyModel, type StrokeModel } from "../src/domain/models";
import { parseMatchModel } from "../src/data/matchParser";
import { createOverlayLoader, parseOverlayChunk, type OverlayChunk } from "../src/overlay/overlayChunk";
import {
  buildOverlayScene, containedRect, type OverlayMark, frameAtMediaTime, frameAtTime, hitFlash, rallyAtFrame, recentStroke, shuttleTrail,
} from "../src/overlay/overlayScene";
import {
  activeOverlayLayers, defaultOverlaySettings, overlayAvailability, overlayShortcut, sanitizeOverlaySettings,
  shownShuttleMethods, toggleOverlayLayer, type OverlayLayerId,
} from "../src/overlay/overlaySettings";
import { defaultWorkspaceLayout, parseWorkspaceLayout } from "../src/state/workspaceLayout";
import { desktopRuntime, registerMatch } from "../scripts/local-matches";
import { startLocalHost } from "../scripts/local-host";

const ALL_ON = { court: true, pose: true, players: true, shuttle: true, trail: true, hit: true, stroke: true };
const MANIFEST = {
  schemaVersion: "review-overlay-v1" as const, url: "/matches/overlay/M-0123456789abcdef", segments: [0, 2],
  methods: ["inpaint", "viterbi"], baseMethod: "inpaint",
  courtLines: [[0, 1]] as [number, number][], skeleton: [[0, 1]] as [number, number][],
};
const usable = { status: "available" as const };

function rawChunk(overrides: Record<string, unknown> = {}) {
  const keypoints = Array.from({ length: 17 }, (_, i) => (i < 2 ? [100 + i, 200 + i] : null));
  return {
    schemaVersion: "review-overlay-v1", segmentIndex: 0, startFrame: 50, frameCount: 4,
    court: Array.from({ length: 16 }, (_, i) => [i, i]),
    shuttle: { inpaint: [[10, 10], [12, 12], null, [16, 16]], viterbi: [null, null, null, [20, 20]] },
    pose: { top: [{ bbox: [90, 180, 130, 260], keypoints }, null, null, null], bottom: [null, null, null, null] },
    ...overrides,
  };
}
const chunk = (): OverlayChunk => parseOverlayChunk(rawChunk(), 0);

function stroke(frame: number, extra: Partial<StrokeModel> = {}): StrokeModel {
  return { eventIndex: frame, strokeIndex: frame, frame, time: frame / 25, ordinal: 1, player: "A",
    type: "殺球", confidence: 0.91, hitter: "a", hitterSide: "top", ...extra };
}
function rally(hits: StrokeModel[] = [], identity?: RallyModel["identity"]): RallyModel {
  return { id: 0, start: 2, end: 2.12, duration: 0.12, score: null, game: null, multi: false, subScores: [], splits: [],
    hits, audio: null, highlight: null, identity,
    commentary: { status: "unavailable", source: null, summary: null, events: [] } };
}

test("overlay settings default off, keep the stroke label unasked, and survive bad storage", () => {
  const defaults = defaultOverlaySettings();
  assert.equal(defaults.enabled, false);
  assert.equal(defaults.layers.stroke, false);
  assert.equal(defaults.layers.court, true);
  assert.equal(defaults.shuttleMethod, "both");
  assert.deepEqual(defaultWorkspaceLayout().overlay, defaults);
  const stored = { ...defaultWorkspaceLayout(), overlay: { enabled: true, layers: { pose: false, court: "yes" }, shuttleMethod: "" } };
  const parsed = parseWorkspaceLayout(JSON.stringify(stored)).overlay;
  assert.equal(parsed.enabled, true);
  assert.equal(parsed.layers.pose, false);
  assert.equal(parsed.layers.court, true);
  assert.equal(parsed.shuttleMethod, "both");
  assert.deepEqual(sanitizeOverlaySettings(null), defaults);
});

test("switching a layer on also switches the overlay on, and keys map to the menu order", () => {
  const settings = defaultOverlaySettings();
  toggleOverlayLayer(settings, "stroke");
  assert.equal(settings.enabled, true);
  settings.enabled = false;
  toggleOverlayLayer(settings, "court");
  assert.equal(settings.layers.court, false);
  assert.equal(settings.enabled, false);
  assert.deepEqual(overlayShortcut({ code: "KeyO", shiftKey: false }), { kind: "overlay" });
  assert.deepEqual(overlayShortcut({ code: "Digit5", shiftKey: false }), { kind: "layer", id: "trail" });
  assert.deepEqual(overlayShortcut({ code: "Numpad1", shiftKey: false }), { kind: "layer", id: "court" });
  assert.equal(overlayShortcut({ code: "Digit8", shiftKey: false }), null);
  assert.equal(overlayShortcut({ code: "KeyO", shiftKey: true }), null);
});

test("layers draw only when on, available, and under a drawn parent", () => {
  const capabilities = capabilityFromStates({ court: usable, pose: usable, shuttle: usable, events: usable });
  const available = overlayAvailability({ capabilities, overlay: MANIFEST, fps: 25 });
  assert.ok(Object.values(available).every((reason) => reason === null));
  const settings = { ...defaultOverlaySettings(), enabled: true };
  settings.layers = { ...settings.layers, shuttle: false };
  const active = activeOverlayLayers(settings, available);
  assert.equal(active.trail, false);
  assert.equal(active.court, true);
  assert.ok(Object.values(activeOverlayLayers(defaultOverlaySettings(), available)).every((on) => !on));

  const oldImport = overlayAvailability({ capabilities, fps: 25 });
  assert.match(oldImport.pose!, /重新匯入/);
  assert.equal(oldImport.hit, null);
  const noPose = overlayAvailability({ capabilities: { ...capabilities, pose: false }, overlay: MANIFEST, fps: 25 });
  assert.match(noPose.players!, /姿態/);
  assert.ok(Object.values(overlayAvailability({ capabilities, overlay: MANIFEST })).every((reason) => reason !== null));
  assert.deepEqual(shownShuttleMethods({ ...settings, shuttleMethod: "viterbi" }, MANIFEST.methods), ["viterbi"]);
  assert.deepEqual(shownShuttleMethods({ ...settings, shuttleMethod: "gone" }, MANIFEST.methods), MANIFEST.methods);
});

test("the Review parser keeps a well-formed overlay manifest and rejects a foreign address", () => {
  const raw = { title: "M", video: "/v", duration: 10, scenario: "s", players: { a: "A", b: "B" },
    states: { segments: usable }, rallies: [] };
  assert.deepEqual(parseMatchModel({ ...raw, overlay: MANIFEST }).overlay, MANIFEST);
  assert.throws(() => parseMatchModel({ ...raw, overlay: { ...MANIFEST, url: "https://example.com/x" } }));
});

test("overlay files are validated against their Rally and frame count", () => {
  const parsed = chunk();
  assert.deepEqual(parsed.shuttle.inpaint?.[0], [10, 10]);
  assert.throws(() => parseOverlayChunk(rawChunk(), 1), /another Rally/);
  assert.throws(() => parseOverlayChunk(rawChunk({ shuttle: { inpaint: [[1, 1]] } }), 0));
  assert.throws(() => parseOverlayChunk(rawChunk({ court: [[0, 0]] }), 0));
  assert.equal(parseOverlayChunk(rawChunk({ pose: {} }), 0).pose.top, undefined);
});

test("the loader fetches each Rally once, skips Rallies without a file, and remembers failures", async () => {
  const fetched: string[] = [];
  const loader = createOverlayLoader(MANIFEST, async (url) => {
    fetched.push(url);
    if (url.endsWith("rally-002.json")) throw new Error("404");
    return rawChunk();
  });
  assert.equal(loader.peek(0), undefined);
  const [first, again] = await Promise.all([loader.load(0), loader.load(0)]);
  assert.equal(first, again);
  assert.equal(loader.peek(0), first);
  assert.equal(await loader.load(1), null);
  assert.equal(await loader.load(2), null);
  assert.equal(await loader.load(2), null);
  assert.deepEqual(fetched, ["/matches/overlay/M-0123456789abcdef/rally-000.json", "/matches/overlay/M-0123456789abcdef/rally-002.json"]);
});

test("frames, Rallies and the letterboxed picture line up with the video", () => {
  assert.equal(frameAtMediaTime(1.9999, 25), 50);
  assert.equal(frameAtMediaTime(2.04, 25), 51);
  assert.equal(frameAtTime(2.035, 25), 50);
  assert.equal(frameAtTime(2.04, 25), 51);
  const rallies = [{ start: 1.9605, end: 9 }, { start: 11, end: 19 }];
  assert.equal(rallyAtFrame(rallies, 49, 25), rallies[0]); // its start second was rounded past the frame
  assert.equal(rallyAtFrame(rallies, 250, 25), null);
  const rect = containedRect({ width: 1000, height: 1000 }, { width: 1920, height: 1080 })!;
  assert.deepEqual([rect.x, rect.y, rect.width, rect.height, rect.scale].map((value) => Number(value.toFixed(6)) + 0),
    [0, 218.75, 1000, 562.5, Number((1000 / 1920).toFixed(6))]);
  assert.equal(containedRect({ width: 0, height: 10 }, { width: 1920, height: 1080 }), null);
});

test("the trail breaks where tracking was lost and starts at its Rally", () => {
  assert.deepEqual(shuttleTrail(chunk(), "inpaint", 53), [[[10, 10], [12, 12]]]);
  assert.deepEqual(shuttleTrail(chunk(), "viterbi", 53), []);
  assert.deepEqual(shuttleTrail(chunk(), "inpaint", 53, 1), []);
  assert.deepEqual(shuttleTrail(null, "inpaint", 51), []);
});

test("hits flash nearest first and stroke labels hold for a moment after their hit", () => {
  const hits = [stroke(50), stroke(55)];
  assert.deepEqual(hitFlash(hits, 52), { hit: hits[0], grow: 6 * 2 });
  assert.equal(hitFlash(hits, 53)?.hit, hits[1]);
  assert.equal(hitFlash(hits, 60), null);
  assert.equal(recentStroke(hits, 54), hits[0]);
  assert.equal(recentStroke(hits, 69), hits[1]);
  assert.equal(recentStroke(hits, 70), null);
  assert.equal(recentStroke(hits, 49), null);
});

const labels = (marks: OverlayMark[]) =>
  marks.filter((mark): mark is Extract<OverlayMark, { kind: "label" }> => mark.kind === "label");

test("the scene names identified players and keeps the court side otherwise", () => {
  const base = { frame: 50, chunk: chunk(), manifest: MANIFEST, layers: ALL_ON, methods: ["inpaint"],
    players: { a: "戴資穎", b: "陳雨菲" } };
  const named = buildOverlayScene({ ...base, rally: rally([stroke(50)], { top: "a", bottom: "b" }) });
  const label = labels(named).find((mark) => mark.align === "start");
  assert.deepEqual(label && { text: label.text, tone: label.tone }, { text: "戴資穎", tone: "a" });
  assert.ok(named.some((mark) => mark.kind === "line" && mark.tone === "a" && mark.width === 2));
  assert.ok(named.some((mark) => mark.kind === "circle" && mark.tone === "hit" && mark.radius === 12 + 24));
  const strokeLabel = labels(named).find((mark) => mark.align === "center");
  assert.deepEqual(strokeLabel && [strokeLabel.text, strokeLabel.at], ["殺球 0.91", [110, 180]]);

  const unnamed = buildOverlayScene({ ...base, rally: rally() });
  const side = labels(unnamed)[0];
  assert.deepEqual(side && { text: side.text, tone: side.tone }, { text: "畫面上方", tone: "neutral" });

  const lost = buildOverlayScene({ ...base, frame: 52, chunk: null, rally: rally([stroke(52, { hitterSide: null })]) });
  assert.deepEqual(lost.map((mark) => mark.kind), ["border", "label"]);
  const off = buildOverlayScene({ ...base, layers: Object.fromEntries(Object.keys(ALL_ON).map((id) => [id, false])) as Record<OverlayLayerId, boolean>, rally: rally([stroke(50)]) });
  assert.deepEqual(off, []);
});

test("import publishes overlay files at their content address and the host serves only those", async () => {
  const root = await mkdtemp(join(tmpdir(), "badminton-overlay-"));
  try {
    const ui = join(root, "ui"), dist = join(ui, "dist"), data = join(root, "data");
    await mkdir(join(dist, "assets"), { recursive: true });
    await writeFile(join(dist, "index.html"), "<html></html>");
    const context = desktopRuntime(ui, join(root, "matches"), root, data, "uv");
    const exported = async (key: string) => {
      const folder = join(root, `export-${key}`);
      await mkdir(folder, { recursive: true });
      await writeFile(join(folder, "rally-000.json"), JSON.stringify({ key }));
      return folder;
    };
    const model = (key: string) => ({ title: "M", overlay: { url: `/matches/overlay/M-${key}` } });
    const video = { path: join(root, "v.mp4"), size: 1, mtimeMs: 1 };
    await registerMatch(context, "M", "M", model("aaaaaaaaaaaaaaaa"), video, await exported("aaaaaaaaaaaaaaaa"));
    await registerMatch(context, "M", "M", model("bbbbbbbbbbbbbbbb"), video, await exported("bbbbbbbbbbbbbbbb"));
    assert.deepEqual(await readdir(join(data, "reviews", "overlay")), ["M-bbbbbbbbbbbbbbbb"]);
    assert.equal(await stat(join(root, "export-bbbbbbbbbbbbbbbb")).then(() => true, () => false), false);
    await assert.rejects(registerMatch(context, "M", "M", { overlay: { url: "/matches/overlay/Other-cccccccccccccccc" } }, video,
      await exported("cccccccccccccccc")), /疊圖/);
    await assert.rejects(registerMatch(context, "M", "M", model("dddddddddddddddd"), video), /疊圖/);

    const host = await startLocalHost(context, dist, { pipelinePort: () => null });
    try {
      const response = await fetch(`${host.origin}/matches/overlay/M-bbbbbbbbbbbbbbbb/rally-000.json`);
      assert.equal(response.status, 200);
      assert.deepEqual(await response.json(), { key: "bbbbbbbbbbbbbbbb" });
      assert.equal((await fetch(`${host.origin}/matches/overlay/M-aaaaaaaaaaaaaaaa/rally-000.json`)).status, 404);
      assert.equal((await fetch(`${host.origin}/matches/overlay/M-bbbbbbbbbbbbbbbb/other.json`)).status, 404);
      assert.equal(JSON.parse(await readFile(join(data, "reviews", "catalog.json"), "utf8")).length, 1);
    } finally { await host.close(); }
  } finally { await rm(root, { recursive: true, force: true }); }
});
