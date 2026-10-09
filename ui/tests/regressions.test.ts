import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  adapt,
  validateFixture,
  manifestSchema,
  type Input,
} from "../scripts/adapter";
import { relatedCommentaryEvents } from "../src/review";
import {
  matchViewport,
  rallyViewport,
  timelineItems,
} from "../src/temporal/timeline";
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
  assert.equal(relatedCommentaryEvents(events, 6).length, 0);
  assert.equal(relatedCommentaryEvents(events, 7).length, 1);
  assert.equal(relatedCommentaryEvents(events, 8).length, 0);
  assert.equal(relatedCommentaryEvents(events, 9).length, 0);
});
test("production commentary keeps summary untimed and events on the shared Timeline", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const workspace = readFileSync("src/state/useReviewWorkspace.ts", "utf8");
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  assert.match(timeline, /rally\.commentary\.events\.map/);
  assert.match(timeline, /time:\s*comment\.timeSec/);
  assert.doesNotMatch(timeline, /commentary\.summary[\s\S]*position\(/);
  assert.match(workspace, /item\.eventIndex === comment\.strokeIndex/);
  assert.match(workspace, /seek\(stroke\.time\)/);
  assert.doesNotMatch(
    `${timeline}\n${workspace}\n${app}`,
    /selectedCommentaryEventIndex/,
  );
  assert.doesNotMatch(app, /生成賽評|Generate Commentary|commentary job/i);
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
test("one-hour 120-segment overview retains exact temporal proportions and gaps", () => {
  const source = Array.from({ length: 120 }, (_, i) => ({
    start_frame: (i * 30 + 2) * 25,
    end_frame: (i * 30 + 3) * 25,
    start_sec: i * 30 + 2,
    end_sec: i * 30 + 3,
    duration_sec: 1,
  }));
  const m = adapt(
    { segments: { fps: 25, segments: source } },
    { ...manifest, duration: 3600, video: "", identities: {} },
    "layout",
  );
  const layout = timelineItems(m.rallies, matchViewport(3600));
  layout.items.forEach((r, i) => {
    assert.equal(r.width, (1 / 3600) * 100);
    assert.equal(r.left, ((i * 30 + 2) / 3600) * 100);
    if (i)
      assert.ok(layout.items[i - 1].left + layout.items[i - 1].width <= r.left);
  });
});

test("rally-fit timeline positions use the selected absolute-time window", () => {
  const rallies = adapt(inputs(), manifest, "full").rallies;
  const selected = rallies[1];
  const layout = timelineItems([selected], rallyViewport(selected));
  assert.equal(layout.items[0].left, 0);
  assert.equal(layout.items[0].width, 100);
});
test("hit labels distinguish stage failure, absence and actual zero", async () => {
  const { hitStatus } = await import("../src/review");
  assert.equal(hitStatus("error", null), "擊球資料讀取失敗");
  assert.equal(hitStatus("missing", null), "未提供擊球資料");
  assert.equal(hitStatus("available", 0), "未偵測到擊球");
});

test("analysis timeline is the only seek surface", () => {
  const player = readFileSync("src/components/ReviewPlayer.vue", "utf8");
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  assert.doesNotMatch(player, /progress-control|aria-label="影片進度"/);
  assert.match(timeline, /@pointerdown="beginScrub"/);
  assert.match(timeline, /@pointermove="inspectPointer\(\$event\); moveScrub\(\$event\)"/);
  assert.match(timeline, /@click="clickTimeline"/);
});

test("Review uses two shared workspace windows with responsive composition", () => {
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  const workspace = readFileSync("src/styles/floating-workspace.css", "utf8");
  assert.equal((app.match(/<WorkspaceWindow/g) ?? []).length, 2);
  assert.match(app, /title="時間軸"/);
  assert.match(app, /title="分析"/);
  assert.match(app, /<AnalysisWindow/);
  assert.match(workspace, /\.review-workspace-stage\s*\{[^}]*position:\s*relative/s);
  assert.match(workspace, /@media \(max-width: 1100px\)[\s\S]*position:\s*relative/);
});

test("timeline exposes three perceptual bands without prototype instruction copy", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  assert.match(timeline, /timeline-band--match/);
  assert.match(timeline, /timeline-band--stroke/);
  assert.match(timeline, /timeline-band--signals/);
  assert.doesNotMatch(timeline, /整場標記密集/);
  assert.match(timeline, /:data-fit="fit"/);
  assert.match(timeline, /class="stroke-label"/);
  assert.doesNotMatch(timeline, /class="fit-controls"/);
  assert.match(timeline, /@dblclick="toggleFitOnDoubleClick"/);
  assert.doesNotMatch(timeline, />\s*Fit (?:Match|Rally)\s*</);
});

test("media progress uses neutral high-contrast playback colors", () => {
  const workspace = readFileSync("src/styles/workspace.css", "utf8");
  assert.match(
    workspace,
    /\.progress-track-fill\s*\{\s*background:\s*var\(--color-media-progress\);/s,
  );
  assert.match(
    workspace,
    /\.progress-thumb\s*\{[^}]*background:\s*var\(--color-media-progress\);/s,
  );
  assert.doesNotMatch(
    workspace,
    /\.progress-track-fill\s*\{\s*background:\s*var\(--color-accent\)/s,
  );
});

test("player preserves source aspect ratio and long analytical text can wrap", () => {
  const player = readFileSync("src/components/ReviewPlayer.vue", "utf8");
  const workspace = readFileSync("src/styles/workspace.css", "utf8");
  const inspector = readFileSync("src/styles/inspector.css", "utf8");
  assert.match(player, /element\.videoWidth} \/ \$\{element\.videoHeight/);
  assert.match(workspace, /object-fit:\s*contain/);
  assert.match(workspace, /\.video-wrap video\s*\{[^}]*max-height:\s*inherit/s);
  assert.match(workspace, /\.player-name\s*\{[^}]*overflow-wrap:\s*anywhere/s);
  assert.match(inspector, /\.hit-player\s*\{[^}]*overflow-wrap:\s*anywhere/s);
});

test("cheer lane draws a mirrored window wave with a threshold, without segment score dots", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const styles = readFileSync("src/styles/timeline.css", "utf8");
  const signalSection = timeline.slice(
    timeline.indexOf('class="timeline-band timeline-band--signals"'),
    timeline.indexOf('class="timeline-playhead"'),
  );
  assert.match(signalSection, /<template v-if="cheerRunList\.length">/);
  assert.match(signalSection, /v-for="\(path, index\) in cheerPaths"/);
  // Solid past the threshold, faint below it, with the threshold drawn.
  assert.match(signalSection, /:data-level="path\.strong \? 'strong' : 'weak'"/);
  assert.match(signalSection, /class="cheer-threshold"/);
  assert.match(signalSection, /無窗口歡呼資料/);
  assert.doesNotMatch(signalSection, /item\.audio|cheer-block/);
  assert.match(styles, /\.cheer-threshold\s*\{[^}]*stroke-dasharray/s);
});

test("highlight has no lane of its own; its place lives in the rally tooltip", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const styles = readFileSync("src/styles/timeline.css", "utf8");
  const preview = readFileSync("src/components/timeline/timelinePreview.ts", "utf8");
  // The lane only ever rendered in the test-only "all" mode, so no one could see it.
  assert.doesNotMatch(timeline, /kind="highlight"|modeShows\('highlight'\)|highlight-block/);
  assert.doesNotMatch(styles, /\.highlight-block|\.signal-block/);
  // A ranking score, never a probability: the tooltip names the place, not the value.
  assert.match(preview, /精華排名 #\$\{rank\}/);
  assert.doesNotMatch(preview, /highlight\.toFixed/);
});

test("rally fit retains the window curve", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const signalStart = timeline.lastIndexOf(
    "<section",
    timeline.indexOf("timeline-band--signals"),
  );
  const signalHeader = timeline.slice(
    signalStart,
    timeline.indexOf('class="timeline-band timeline-band--signals"'),
  );
  assert.match(signalHeader, /v-if="showCheerCurveLane"/);
  assert.match(timeline, /showCheerCurveLane\.value \? 1 : 1 - lensProgress\.value/);
  assert.match(timeline, /v-if="showCheerCurveLane"/);
  assert.doesNotMatch(signalHeader, /fit === 'rally'/);
});

test("score lead lane draws non-text marks in SVG so scrolling never pixel-snaps them", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const styles = readFileSync("src/styles/timeline.css", "utf8");
  assert.doesNotMatch(timeline, /<span[^>]*class="lead-(?:focus|separator|uncertain)/);
  assert.match(timeline, /<svg\s+v-for="change in leadMarks\.changes"/);
  assert.doesNotMatch(timeline, /leadMarks\.points/, "game points live in the hover text, not as marks");
  assert.doesNotMatch(styles, /\.lead-mark[^{]*i/);
  // Chips carry a 1px divider; only a self-composited transform moves it by subpixels.
  assert.match(timeline, /class="score-state timeline-score-state lead-chip"[\s\S]{0,80}:style="\{ transform: `translateX\(calc\(\$\{chip\.x\}cqw - 50%\)\)` \}"/);
  assert.match(styles, /\.lead-chip \{[^}]*will-change: transform;/s);
  assert.doesNotMatch(timeline, /rotate\(45\)/, "no match point diamond");
});

test("score lead lane plots segment observations without deriving transitions", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const lead = readFileSync("src/temporal/scoreLead.ts", "utf8");
  assert.match(timeline, /const leadModel = computed\(\(\) => scoreLeadModel\(props\.model\.rallies\)\)/);
  assert.match(timeline, /class="lead-mark lead-mark--change"[\s\S]*:aria-label=/);
  assert.doesNotMatch(timeline, /比分事件|比分轉換/);
  // A step starts at its own Rally: the observation is the pre-Rally score.
  assert.match(lead, /start: rally\.start,/);
  assert.doesNotMatch(lead, /winnerFromTransition|連得/);
});

test("temporal map owns an opaque analytical surface", () => {
  const timeline = readFileSync("src/styles/timeline.css", "utf8");
  const workspace = readFileSync("src/styles/workspace.css", "utf8");
  assert.match(
    timeline,
    /\.intelligence-timeline\s*\{[^}]*background:\s*var\(--color-surface\)[^}]*border-top:/s,
  );
  assert.doesNotMatch(
    workspace,
    /\.focus-mode \.timeline-zone\s*\{[^}]*background:\s*transparent/s,
  );
});

test("selected stroke summary is stable before the list and keeps seek emission", () => {
  const detail = readFileSync(
    "src/components/inspector/RallyDetail.vue",
    "utf8",
  );
  const summaryAt = detail.indexOf('v-if="selectedStroke" class="hit-reading"');
  const listAt = detail.indexOf('class="hit-list"');
  assert.ok(summaryAt > 0 && summaryAt < listAt);
  assert.equal((detail.match(/class="hit-reading"/g) ?? []).length, 1);
  assert.doesNotMatch(
    detail,
    /v-if="selectedStrokeIndex === stroke\.eventIndex"\s+class="hit-reading"/,
  );
  assert.match(detail, /@click="emit\('stroke', stroke\)"/);
});

test("timeline labels, inspector provenance and workspace guidance use finished semantics", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const detail = readFileSync(
    "src/components/inspector/RallyDetail.vue",
    "utf8",
  );
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  for (const removed of [
    "比賽結構",
    "片段與比分",
    "密度紋理",
    "順序與內容",
    "分析訊號",
    "歡呼與精華",
  ]) {
    assert.doesNotMatch(timeline, new RegExp(removed));
  }
  assert.doesNotMatch(detail, /（身分推導）/);
  assert.match(detail, /局數來源：/);
  assert.match(detail, /rally\.game !== null && rally\.gameSource/);
  assert.match(detail, /gameSourceLabel\(rally\.gameSource\)/);
});

test("instrument typography bundles only the compact measurement face", () => {
  const packageJson = JSON.parse(readFileSync("package.json", "utf8"));
  const tokens = readFileSync("src/styles/tokens.css", "utf8");
  assert.equal(packageJson.dependencies["@ibm/plex-sans-tc"], undefined);
  assert.equal(packageJson.dependencies["@fontsource/ibm-plex-mono"], "^5.3.0");
  assert.match(tokens, /font-family: "IBM Plex Mono";/);
  assert.equal((tokens.match(/font-display: swap;/g) ?? []).length, 2);
  assert.match(tokens, /--font-interface: "Segoe UI"/);
  assert.match(tokens, /--font-measurement: "IBM Plex Mono"/);
  assert.match(tokens, /--radius-sm: 8px;/);
  assert.match(tokens, /--radius-lg: 18px;/);
});

test("primary scores use the instrument divider while timeline semantics stay unchanged", () => {
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  const detail = readFileSync(
    "src/components/inspector/RallyDetail.vue",
    "utf8",
  );
  const inspector = readFileSync(
    "src/components/workspace/AnalysisWindow.vue",
    "utf8",
  );
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  for (const primary of [app, detail]) {
    assert.match(primary, /class="score-state[^"]*"/);
    assert.match(
      primary,
      /<span class="score-state-divider" aria-hidden="true" \/>/,
    );
    assert.doesNotMatch(primary, />┃</);
  }
  assert.match(inspector, /class="analysis-context-header"/);
  assert.match(inspector, /analysisContextSummary/);
  assert.match(timeline, /class="score-state timeline-score-state lead-chip"/);
  assert.match(timeline, /class="score-state-divider" \/>/);
  assert.match(timeline, /class="lead-mark lead-mark--change"[\s\S]*data-timeline-kind="score"/);
  assert.match(app, /workspace\.currentScore\.value/);
  assert.doesNotMatch(app, /replace\(\/\\s\*:\\s\*\//);
});

test("temporal lens interpolates only presentation between authoritative viewports", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const styles = readFileSync("src/styles/timeline.css", "utf8");
  assert.match(
    timeline,
    /renderViewport = ref<TimelineViewport>\(\s*fitViewport\("match", props\.model\.duration, null\)/,
  );
  assert.match(
    timeline,
    /return fitViewport\(next, props\.model\.duration, selectedRally\.value\)/,
  );
  assert.match(timeline, /const LENS_DURATION_MS = 220;/);
  assert.match(timeline, /requestAnimationFrame\(step\)/);
  assert.match(timeline, /cancelAnimationFrame\(lensFrame\)/);
  assert.match(timeline, /const request = \+\+lensRequest/);
  assert.match(timeline, /if \(request !== lensRequest\) return/);
  assert.match(timeline, /data-lens-active="lensActive"/);
  assert.match(
    styles,
    /\[data-lens-active="true"\] \.timeline-surface\s*\{[^}]*pointer-events:\s*none/s,
  );
  assert.doesNotMatch(timeline, /currentTime|\.pause\(|\.play\(|seek\(/);
  assert.doesNotMatch(styles, /\.timeline-playhead\s*\{[^}]*transition:/s);
});

test("temporal lens reduced-motion path applies the requested endpoint immediately", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  assert.match(
    timeline,
    /window\.matchMedia\("\(prefers-reduced-motion: reduce\)"\)/,
  );
  assert.match(
    timeline,
    /if \(reducedMotionQuery\?\.matches\) \{\s*applyLensEndpoint\(target, targetProgress\);\s*return;/s,
  );
  assert.match(
    timeline,
    /handleReducedMotionChange[\s\S]*cancelLens\(\);[\s\S]*applyLensEndpoint/,
  );
});

test("motion tokens and optical score rule stay centralized", () => {
  const tokens = readFileSync("src/styles/tokens.css", "utf8");
  const workspace = readFileSync("src/styles/workspace.css", "utf8");
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  assert.match(timeline, /const LENS_DURATION_MS = 220;/);
  assert.match(tokens, /--motion-ease: cubic-bezier\(0\.2, 0, 0, 1\);/);
  assert.match(
    tokens,
    /\.score-state-divider\s*\{[^}]*width:\s*1px;[^}]*height:\s*0\.7em;[^}]*background:\s*var\(--line-selected\);/s,
  );
  assert.match(
    workspace,
    /\.play-button \.app-icon--play\s*\{[^}]*translateX\(0\.75px\)/s,
  );
});

test("signature score remains an editorial axis in the Match Bar", () => {
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  const workspace = readFileSync("src/styles/workspace.css", "utf8");
  assert.match(app, /class="score-state match-score-state"/);
  assert.match(app, /v-if="headerScore"/);
  assert.match(
    workspace,
    /\.match-identity\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto minmax\(0, 1fr\);/s,
  );
  assert.match(
    workspace,
    /\.match-score-context strong\s*\{[^}]*font-size:\s*32px;[^}]*line-height:\s*34px;/s,
  );
});

test("temporal signature remains shape-led and preserves semantic hierarchy", () => {
  const timeline = readFileSync("src/styles/timeline.css", "utf8");
  // The rally lane is neutral so it never spoils a match being watched; winners live in the hover text.
  assert.match(timeline, /\.rally-bar\s*\{[^}]*fill:\s*var\(--color-rally-hover\);/s);
  assert.doesNotMatch(timeline, /\.rally-bar[^{]*\{[^}]*--color-player-/s);
  assert.doesNotMatch(readFileSync("src/components/ReviewTimeline.vue", "utf8"), /data-winner|連得/);
  assert.match(timeline, /\.lead-chart__line,\s*\.lead-chart__gap\s*\{[^}]*stroke:\s*var\(--color-score\);/s);
  assert.match(timeline, /\.lead-chart__area\s*\{[^}]*fill:\s*var\(--color-player-a\);/s);
  // Stroke families have their own hues; the A/B colours mean a player's side, so the trace's height carries the hitter.
  assert.match(timeline, /\.stroke-bar\s*\{[^}]*fill:\s*var\(--shot\);/s);
  assert.doesNotMatch(timeline, /\.stroke-[^{]*\{[^}]*--color-player-/s);
  assert.doesNotMatch(readFileSync("src/styles/tokens.css", "utf8"), /--color-shot-[a-z]+:\s*var\(--color-player-/);
  assert.match(timeline, /\.cheer-wave\s*\{[^}]*fill:\s*var\(--color-cheer\)/s);
  assert.match(
    timeline,
    /\.timeline-playhead\s*\{[^}]*border-left:\s*2px solid var\(--color-playhead\);/s,
  );
});

test("signature interaction uses one guarded shortcut path and lane delegation", () => {
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  const player = readFileSync("src/components/ReviewPlayer.vue", "utf8");
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const composable = readFileSync("src/composables/usePlayer.ts", "utf8");
  assert.match(app, /function shortcutBlocked\(event: KeyboardEvent\)/);
  assert.match(app, /window\.addEventListener\("keydown", keyboard\)/);
  assert.doesNotMatch(composable, /addEventListener\("keydown"/);
  assert.match(player, /@click="toggle"/);
  assert.match(player, /<\/div>\s*<Teleport [^>]*>\s*<div class="controls">/);
  assert.doesNotMatch(player, /progress-rally-window|progress-stroke-tick/);
  assert.match(timeline, /@pointerdown="beginScrub"/);
  assert.match(timeline, /moveScrub\(\$event\)/);
  assert.match(timeline, /@click="clickTimeline"/);
  assert.match(timeline, /emit\(\s*"rallyAt",[\s\S]*timeSec/);
  assert.match(
    timeline,
    /if \(kind === "score"\)[\s\S]*emit\("rally", rally\)/,
  );
  assert.match(
    timeline,
    /if \(kind === "stroke"\)[\s\S]*emit\("stroke", stroke\)/,
  );
  assert.match(
    timeline,
    /if \(kind === "cheer"\) \{\s*emit\("seek", timeSec\)/,
  );
  assert.match(timeline, /else emit\("seek", timeSec\)/);
  assert.doesNotMatch(timeline, /<button\s+class="rally-block"/);
  assert.doesNotMatch(timeline, /<button\s+v-for="rally in scoreRallies"/);
});

test("temporal coherence adds registration without changing temporal ownership", () => {
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  const player = readFileSync("src/components/ReviewPlayer.vue", "utf8");
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const timelineStyles = readFileSync("src/styles/timeline.css", "utf8");
  assert.doesNotMatch(player, /progress-rally-locator|progress-stroke-tick/);
  assert.match(app, /@time="workspace\.updateTime"/);
  assert.match(timeline, /class="rally-bar"[\s\S]{0,400}:x="bar\.x \+ '%'"[\s\S]{0,60}:width="bar\.width \+ '%'"/);

  assert.match(timeline, /hoveredMark = ref/);
  assert.match(timeline, /resolveHoveredMark/);
  assert.match(timeline, /nearestTemporalMark/);
  assert.match(timeline, /<rect class="lead-focus" :data-state="band\.state" :x="band\.left"/);
  assert.match(
    timelineStyles,
    /\.timeline-inspection > span\s*\{[^}]*background:\s*transparent;[^}]*border:\s*0;[^}]*border-radius:\s*0;[^}]*box-shadow:\s*none;/s,
  );
  assert.match(
    timelineStyles,
    /\.timeline-lane:hover \.timeline-lane-track\s*\{[^}]*border-bottom-color:/s,
  );
  assert.doesNotMatch(
    timeline,
    /@pointer(?:enter|move)="[^\"]+"[^>]+(?:rally-block|score-marker|stroke-tick|signal-block)/,
  );
  assert.doesNotMatch(app, /headerRally \? currentLabel : "比賽回看"/);
  assert.match(
    app,
    /<small>\{\{ currentLabel \}\}<\/small>/,
  );
});

test("Rally registration stays on the shared timeline", () => {
  const player = readFileSync("src/components/ReviewPlayer.vue", "utf8");
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  const timelineStyles = readFileSync("src/styles/timeline.css", "utf8");
  assert.doesNotMatch(player, /progress-rally-locator/);
  // Numbers every Rally the zoom has room for, so they never come and go while playback scrolls.
  assert.match(timeline, /fit\.value === "match" \|\| lensActive\.value\s*\? \[\]\s*: rallyIndexLabels\(/);
  assert.match(timeline, /class="rally-registration-index rally-index"[\s\S]{0,160}translateX\(calc\(\$\{label\.x\}cqw - 50%\)\)/);
  assert.doesNotMatch(timeline, /rally-block-locator/);
  assert.match(timelineStyles, /\.rally-index \{[^}]*will-change: transform;/s);
});

test("editorial inspector keeps content order and stable measurement columns", () => {
  const detail = readFileSync(
    "src/components/inspector/RallyDetail.vue",
    "utf8",
  );
  const inspector = readFileSync("src/styles/inspector.css", "utf8");
  const scoreAt = detail.indexOf('class="score-section"');
  const factsAt = detail.indexOf('class="rally-facts"');
  const signalsAt = detail.indexOf('class="derived-signals"');
  const commentaryAt = detail.indexOf('class="commentary-empty"');
  const strokesAt = detail.indexOf('class="section-heading hit-heading"');
  const sourceAt = detail.indexOf('class="source-details"');
  assert.ok(
    scoreAt < factsAt &&
      factsAt < signalsAt &&
      signalsAt < commentaryAt &&
      commentaryAt < strokesAt &&
      strokesAt < sourceAt,
  );
  assert.match(detail, /padStart\(3, "0"\)/);
  assert.match(detail, /padStart\(2, "0"\)/);
  assert.match(
    inspector,
    /\.hit-list > button\s*\{[^}]*grid-template-columns:\s*30px 78px minmax\(68px, 1fr\) minmax\(56px, auto\);/s,
  );
  assert.match(
    inspector,
    /\.commentary-empty h3::after\s*\{[^}]*border-top:\s*1px solid var\(--line-structure\);/s,
  );
});

test("fit, active, and selected states use the line selection grammar", () => {
  const timeline = readFileSync("src/styles/timeline.css", "utf8");
  const inspector = readFileSync("src/styles/inspector.css", "utf8");
  assert.match(
    inspector,
    /\.hit-list > button\.selected\s*\{[^}]*background:\s*transparent;[^}]*border-left-color:\s*var\(--line-selected\);/s,
  );
  assert.match(
    inspector,
    /\.derived-signals h3::after\s*\{[^}]*border-top:\s*1px solid var\(--line-structure\);/s,
  );
});

test("foundation controls retain forty-pixel targets and visible keyboard focus", () => {
  const tokens = readFileSync("src/styles/tokens.css", "utf8");
  assert.match(tokens, /--control-normal: 40px;/);
  assert.match(
    tokens,
    /button\s*\{[^}]*min-height:\s*var\(--control-normal\);/s,
  );
  assert.match(
    tokens,
    /button:focus-visible,[\s\S]*outline:\s*2px solid var\(--color-text\);[\s\S]*outline-offset:\s*2px;/,
  );
});

test("Match Bar and player context never fall back to stale selection", () => {
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  assert.match(
    app,
    /const activeId = computed\(\(\) => workspace\.activeRally\.value\?\.id \?\? null\);/,
  );
  assert.match(app, /const currentLabel = computed\(\(\) => activeId\.value === null \? "比賽空檔"/);
  assert.match(app, /workspace\.currentScore\.value/);
  assert.doesNotMatch(
    app,
    /activeRally\.value \?\? workspace\.selectedRally\.value/,
  );
});

test("timeline has no header row: no track filter, fit toggle, summary, or return button", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  assert.doesNotMatch(timeline, /class="timeline-header"/);
  assert.doesNotMatch(timeline, /顯示軌道|回到目前|filters\./);
  assert.doesNotMatch(timeline, /段 ·/);
  assert.doesNotMatch(timeline, /surfaceWidth\.value - 92/);
  assert.match(timeline, /measureTrackWidth/);
  assert.match(timeline, /scheduleInspectionRestore/);
});

test("source disclosure uses product labels and makes no unsupported provenance claim", () => {
  const detail = readFileSync(
    "src/components/inspector/RallyDetail.vue",
    "utf8",
  );
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  assert.match(detail, /segments: "片段"/);
  assert.match(detail, /audio_signals: "歡呼訊號"/);
  assert.match(detail, /available: "可用"/);
  assert.match(detail, /missing: "未提供"/);
  assert.match(detail, /error: "讀取失敗"/);
  assert.match(detail, /rally\.game !== null && rally\.gameSource/);
  assert.doesNotMatch(detail, /state\.status\s*}}/);
  assert.doesNotMatch(app, /首屏不載入|AI 賽評/);
});

test("Escape priority and IME safety remain explicit after routed Match selection", () => {
  const app = readFileSync("src/pages/ReviewPage.vue", "utf8");
  assert.match(app, /event\.isComposing/);
  assert.match(app, /if \(workspace\.selectedRallyIndex\.value !== null\)[\s\S]*workspace\.clearSelection/);
  assert.match(app, /"input,select,textarea,\[contenteditable\]"/);
  assert.doesNotMatch(app, /libraryOpen|closeLibrary|MatchLibrary/);
});

test("player resets source presentation while timeline owns arbitrary seek", () => {
  const player = readFileSync("src/components/ReviewPlayer.vue", "utf8");
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  assert.match(player, /mediaAspectRatio\.value = "16 \/ 9"/);
  assert.match(player, /muted\.value = element\?\.muted \?\? false/);
  assert.doesNotMatch(player, /step="any"|rallyLocatorSide/);
  assert.match(timeline, /function scrubTime\(clientX: number, track: HTMLElement\)/);
});

test("score labels have a dedicated exact-identity interaction target", () => {
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  assert.match(timeline, /@click\.stop="selectScoreMarker\(change\.rallyId\)"/);
  assert.match(timeline, /exactMarkFromTarget/);
  assert.match(timeline, /scoreLaneRally/);
});

test("Review inspector keeps no embedded list bookkeeping", () => {
  const inspector = readFileSync(
    "src/components/inspector/RallyInspector.vue",
    "utf8",
  );
  assert.doesNotMatch(inspector, /RallyBrowser/);
  assert.doesNotMatch(inspector, /listScrollTop|returnFocusId/);
});

test("exact Stroke evidence uses one precise formatter", () => {
  const detail = readFileSync(
    "src/components/inspector/RallyDetail.vue",
    "utf8",
  );
  const player = readFileSync("src/components/ReviewPlayer.vue", "utf8");
  const timeline = readFileSync("src/components/ReviewTimeline.vue", "utf8");
  assert.match(detail, /formatPreciseTime\(selectedStroke\.time\)/);
  assert.match(detail, /formatPreciseTimeParts\(stroke\.time\)\.fraction/);
  // The player carries no stroke HUD or segment summary; stroke evidence lives in the inspector.
  assert.doesNotMatch(player, /evidence-hud|now-playing|activeStroke/);
  // Timeline stroke labels carry only the shot; the side of the net line already says who hit it,
  // and exact time and player name live in the inspector and hover preview.
  assert.doesNotMatch(timeline, /formatPreciseTime\(stroke\.time\)/);
  assert.doesNotMatch(timeline, /stroke-marker-index/);
  assert.doesNotMatch(timeline, /playerName\(stroke\.player\)/);
  assert.doesNotMatch(detail, /Math\.round\(\(stroke\.time % 1\) \* 100\)/);
});
