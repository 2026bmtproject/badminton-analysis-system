import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  adaptiveDefaultPanelSizes,
  resolveAnalysisDensity,
  resolveTimelineDensity,
  timelineTicks,
} from "../src/presentation/workspaceDensity";
import { placeWorkspacePopover } from "../src/presentation/workspacePopover";

const source = (path: string) => readFileSync(path, "utf8");

test("Timeline size only chooses full or compact", () => {
  assert.equal(resolveTimelineDensity(900, 230), "full");
  assert.equal(resolveTimelineDensity(900, 116), "full");
  assert.equal(resolveTimelineDensity(900, 100), "compact");
  assert.equal(resolveTimelineDensity(620, 170), "compact");
  assert.equal(resolveTimelineDensity(330, 170), "compact");
  assert.equal(resolveTimelineDensity(900, 90), "compact");
});

test("adaptive defaults prioritize video on constrained desktop viewports", () => {
  assert.deepEqual(adaptiveDefaultPanelSizes(1920, 1080), { analysisWidth: 340, timelineHeight: 160 });
  assert.deepEqual(adaptiveDefaultPanelSizes(1440, 900), { analysisWidth: 340, timelineHeight: 160 });
  assert.deepEqual(adaptiveDefaultPanelSizes(1366, 768), { analysisWidth: 260, timelineHeight: 160 });
  assert.deepEqual(adaptiveDefaultPanelSizes(1280, 720), { analysisWidth: 260, timelineHeight: 160 });
});

test("pixel-aware ticks reduce count while retaining valid ordered ranges", () => {
  const wide = timelineTicks(0, 600, 1_000, "full");
  const narrow = timelineTicks(0, 600, 380, "compact");
  assert.ok(wide.length > narrow.length);
  assert.ok(narrow.length >= 2);
  assert.ok(wide.every((tick, index) => index === 0 || tick.timeSec > wide[index - 1]!.timeSec));
  assert.ok(wide.every((tick) => tick.percent >= 0 && tick.percent <= 100));
});

test("scrolling ticks move their labels continuously and fade where the view cuts the match", () => {
  const labelLeft = (tick: { percent: number; shift: number }) => tick.percent * 10 + (tick.shift / 100) * 40;
  let previous: number | null = null;
  for (let start = 100; start <= 104; start += 0.05) {
    const tick = timelineTicks(start, start + 20, 1_000, "full", 600).find((item) => item.timeSec === 110)!;
    const left = labelLeft(tick);
    if (previous !== null) assert.ok(Math.abs(previous - left) < 4, `label jumped ${previous} -> ${left}`);
    previous = left;
  }
  const scrolled = timelineTicks(101.99, 121.99, 1_000, "full", 600);
  assert.ok(scrolled[0]!.opacity < 0.1, "a tick entering at the cut edge starts transparent");
  assert.ok(scrolled.some((tick) => tick.opacity === 1));
  const match = timelineTicks(0, 600, 1_000, "full", 600);
  assert.equal(match[0]!.opacity, 1, "match boundaries never fade");
  assert.equal(match.at(-1)!.opacity, 1);
});

test("Analysis resize resolves full or compact without becoming a collapsed rail", () => {
  assert.equal(resolveAnalysisDensity(340), "full");
  assert.equal(resolveAnalysisDensity(260), "compact");
  assert.equal(resolveAnalysisDensity(180), "compact");
  assert.match(source("src/components/workspace/AnalysisWindow.vue"), /data-density/);
  assert.match(source("src/components/workspace/AnalysisWindow.vue"), /activeId/);
  assert.doesNotMatch(source("src/components/workspace/AnalysisWindow.vue"), /analysis-density-rail|writing-mode/);
});

test("responsive presentation removes DOM horizontal timeline navigation", () => {
  const timelineStyles = source("src/styles/timeline.css");
  assert.match(timelineStyles, /overflow-x:\s*clip/);
});

test("product chrome omits raw match identity and permanent technical footer", () => {
  const shell = source("src/layouts/MatchShell.vue");
  const review = source("src/pages/ReviewPage.vue");
  assert.doesNotMatch(shell, /<code v-if="entry"/);
  assert.match(shell, /比賽回看/);
  assert.match(review, /shortcut-help__content/);
  assert.doesNotMatch(review, /workspace-method-details/);
});

test("shared teleported popover has independent width and viewport collision placement", () => {
  const component = source("src/components/workspace/WorkspacePopover.vue");
  const styles = source("src/styles/floating-workspace.css");
  assert.match(component, /<Teleport :to="teleportTarget">/);
  assert.match(component, /document\.fullscreenElement instanceof HTMLElement/);
  assert.match(styles, /\.workspace-popover[^}]*position:\s*fixed/s);
  assert.match(styles, /white-space:\s*nowrap/);
  const placed = placeWorkspacePopover(
    { left: 1240, right: 1272, top: 680, bottom: 712 },
    { width: 210, height: 140 },
    { width: 1280, height: 720 },
    200,
  );
  assert.ok(placed.left + placed.width <= 1272);
  assert.ok(placed.top < 680);
});

test("gap state uses concise product copy", () => {
  const inspector = source("src/components/inspector/RallyInspector.vue");
  assert.match(inspector, /等待下一段/);
  assert.doesNotMatch(inspector, /保留最新可用比分|不顯示前一段內容/);
});

test("splitters use neutral structural tokens", () => {
  const styles = source("src/styles/floating-workspace.css");
  const splitter = styles.slice(
    styles.indexOf(".workspace-window__dock-resize"),
    styles.indexOf(".workspace-window__resize-grip::after"),
  );
  assert.match(splitter, /--line-strong|--color-border-strong/);
  assert.doesNotMatch(splitter, /--color-accent|--line-active/);
});
