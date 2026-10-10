import { effectScope, reactive, watch } from "vue";

export type WorkspacePanelId = "timeline" | "analysis";
export type TimelineMode = "rally" | "stroke" | "score" | "cheer";
export type AnalysisView = "analysis" | "court";

export type PanelLayout = {
  x: number;
  y: number;
  width: number;
  height: number;
  collapsed: boolean;
  alpha: number;
};

export type WorkspaceLayout = {
  version: 3;
  timelineMode: TimelineMode;
  analysisView: AnalysisView;
  /** Playback skips the gaps between Segments and stops after the last one. */
  segmentsOnly: boolean;
  /** The Analysis window folds its stroke list away, leaving the header and commentary. */
  strokeListCollapsed: boolean;
  /** Seconds without input before fullscreen hides both floating windows, or null to keep them shown; one value for both. */
  fullscreenIdleSec: number | null;
  analysisDockWidth: number;
  timelineDockHeight: number;
  panels: Record<WorkspacePanelId, PanelLayout>;
  fullscreenPanels: Record<WorkspacePanelId, PanelLayout>;
};

export const WORKSPACE_STORAGE_KEY = "badminton-review-workspace-v1";
export const DEFAULT_ANALYSIS_DOCK_WIDTH = 340;
export const DEFAULT_TIMELINE_DOCK_HEIGHT = 200;
export const MIN_ANALYSIS_DOCK_WIDTH = 180;
export const MAX_ANALYSIS_DOCK_WIDTH = 460;
export const MIN_TIMELINE_DOCK_HEIGHT = 96;
export const MAX_TIMELINE_DOCK_HEIGHT = 380;
/** Fullscreen panel background alpha: from 80% transparent to fully opaque. */
export const MIN_PANEL_ALPHA = 0.2;
export const MAX_PANEL_ALPHA = 1;
export const DEFAULT_FULLSCREEN_IDLE_SEC = 3;
export const MIN_FULLSCREEN_IDLE_SEC = 1;
export const MAX_FULLSCREEN_IDLE_SEC = 10;

/**
 * Normal mode always docks both panels, fully open: only fullscreen windows collapse.
 * Fullscreen always floats them with these geometries and alphas.
 */
const panelDefaults: Record<WorkspacePanelId, PanelLayout> = {
  timeline: { x: 0.025, y: 0.64, width: 0.70, height: 0.32, collapsed: false, alpha: 0.94 },
  analysis: { x: 0.735, y: 0.035, width: 0.24, height: 0.57, collapsed: false, alpha: 0.94 },
};

/**
 * Earlier fullscreen timeline defaults. An unresized legacy timeline adopts the
 * current default height, and one still at its legacy spot adopts the current
 * default position, which sits lower now that no player bar spans the screen edge.
 */
const LEGACY_FULLSCREEN_TIMELINES = [
  { y: 0.66, height: 0.25 },
  { y: 0.72, height: 0.19 },
  { y: 0.74, height: 0.23 },
];

const modes: TimelineMode[] = ["rally", "stroke", "score", "cheer"];
const views: AnalysisView[] = ["analysis", "court"];

function finite(value: unknown, fallback: number) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}
function clamp(value: unknown, fallback: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, finite(value, fallback)));
}
export function constrainAnalysisDockWidth(value: number) {
  return clamp(value, DEFAULT_ANALYSIS_DOCK_WIDTH, MIN_ANALYSIS_DOCK_WIDTH, MAX_ANALYSIS_DOCK_WIDTH);
}
/** Null (never hide) stays null; anything else becomes whole seconds within the slider's range. */
export function constrainFullscreenIdleSec(value: unknown): number | null {
  if (value === null) return null;
  return Math.round(clamp(value, DEFAULT_FULLSCREEN_IDLE_SEC, MIN_FULLSCREEN_IDLE_SEC, MAX_FULLSCREEN_IDLE_SEC));
}
export function constrainTimelineDockHeight(value: number) {
  return clamp(value, DEFAULT_TIMELINE_DOCK_HEIGHT, MIN_TIMELINE_DOCK_HEIGHT, MAX_TIMELINE_DOCK_HEIGHT);
}

export function sanitizePanel(value: unknown, fallback: PanelLayout): PanelLayout {
  const panel = value && typeof value === "object" ? (value as Partial<PanelLayout>) : {};
  const width = Math.min(0.96, Math.max(0.18, finite(panel.width, fallback.width)));
  const height = Math.min(0.92, Math.max(0.10, finite(panel.height, fallback.height)));
  return {
    x: Math.min(1 - width, Math.max(0, finite(panel.x, fallback.x))),
    y: Math.min(1 - height, Math.max(0, finite(panel.y, fallback.y))),
    width,
    height,
    collapsed: typeof panel.collapsed === "boolean" ? panel.collapsed : fallback.collapsed,
    alpha: clamp(panel.alpha, fallback.alpha, MIN_PANEL_ALPHA, MAX_PANEL_ALPHA),
  };
}

export function defaultWorkspaceLayout(): WorkspaceLayout {
  return JSON.parse(JSON.stringify({
    version: 3,
    timelineMode: "rally",
    analysisView: "analysis",
    segmentsOnly: false,
    strokeListCollapsed: false,
    fullscreenIdleSec: DEFAULT_FULLSCREEN_IDLE_SEC,
    analysisDockWidth: DEFAULT_ANALYSIS_DOCK_WIDTH,
    timelineDockHeight: DEFAULT_TIMELINE_DOCK_HEIGHT,
    panels: {
      timeline: { ...panelDefaults.timeline },
      analysis: { ...panelDefaults.analysis },
    },
    fullscreenPanels: {
      timeline: { x: 0.025, y: 0.78, width: 0.69, height: 0.19, collapsed: false, alpha: 0.94 },
      analysis: { ...panelDefaults.analysis },
    },
  })) as WorkspaceLayout;
}

export function parseWorkspaceLayout(raw: string | null): WorkspaceLayout {
  const fallback = defaultWorkspaceLayout();
  if (!raw) return fallback;
  try {
    const value = JSON.parse(raw) as Omit<Partial<WorkspaceLayout>, "version"> & { version?: number };
    if (value.version !== 1 && value.version !== 2 && value.version !== 3) return fallback;
    const fullscreenTimeline = sanitizePanel(value.fullscreenPanels?.timeline, fallback.fullscreenPanels.timeline);
    const legacy = LEGACY_FULLSCREEN_TIMELINES.find((spot) => spot.height === fullscreenTimeline.height && spot.y === fullscreenTimeline.y)
      ?? LEGACY_FULLSCREEN_TIMELINES.find((spot) => spot.height === fullscreenTimeline.height);
    if (legacy) {
      if (fullscreenTimeline.y === legacy.y) fullscreenTimeline.y = fallback.fullscreenPanels.timeline.y;
      fullscreenTimeline.height = fallback.fullscreenPanels.timeline.height;
    }
    return {
      version: 3,
      timelineMode: modes.includes(value.timelineMode as TimelineMode) ? value.timelineMode! : "rally",
      analysisView: views.includes(value.analysisView as AnalysisView) ? value.analysisView! : "analysis",
      segmentsOnly: value.segmentsOnly === true,
      strokeListCollapsed: value.strokeListCollapsed === true,
      fullscreenIdleSec: constrainFullscreenIdleSec(value.fullscreenIdleSec),
      analysisDockWidth: constrainAnalysisDockWidth(finite(value.analysisDockWidth, DEFAULT_ANALYSIS_DOCK_WIDTH)),
      timelineDockHeight: constrainTimelineDockHeight(finite(value.timelineDockHeight, DEFAULT_TIMELINE_DOCK_HEIGHT)),
      panels: {
        timeline: { ...sanitizePanel(value.panels?.timeline, fallback.panels.timeline), collapsed: false },
        analysis: { ...sanitizePanel(value.panels?.analysis, fallback.panels.analysis), collapsed: false },
      },
      fullscreenPanels: {
        timeline: fullscreenTimeline,
        analysis: sanitizePanel(value.fullscreenPanels?.analysis, fallback.fullscreenPanels.analysis),
      },
    };
  } catch {
    return fallback;
  }
}

let shared: { layout: WorkspaceLayout } | null = null;

/**
 * One layout for the whole app, so the settings page and a kept-alive Review edit the same values. Its saving watcher
 * lives in a detached scope: it must outlast whichever view asked first.
 */
export function useWorkspaceLayout() {
  if (shared) return shared;
  const stored = typeof localStorage === "undefined" ? null : localStorage.getItem(WORKSPACE_STORAGE_KEY);
  const layout = reactive(parseWorkspaceLayout(stored));

  if (typeof localStorage !== "undefined") {
    effectScope(true).run(() => {
      watch(layout, (value) => localStorage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(value)), { deep: true });
    });
  }
  shared = { layout };
  return shared;
}

/** Docked sizes and the fullscreen windows' places, sizes and transparency go back to their defaults. */
export function resetWorkspaceWindows(layout: WorkspaceLayout) {
  const defaults = defaultWorkspaceLayout();
  layout.analysisDockWidth = defaults.analysisDockWidth;
  layout.timelineDockHeight = defaults.timelineDockHeight;
  layout.fullscreenPanels = defaults.fullscreenPanels;
}
