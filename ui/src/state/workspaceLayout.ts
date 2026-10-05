import { reactive, watch } from "vue";

export type WorkspacePanelId = "timeline" | "analysis";
export type PanelPresentation = "docked" | "detached";
export type AnalysisDockSide = "left" | "right";
export type TimelineMode = "rally" | "stroke" | "score" | "commentary" | "cheer";
export type AnalysisView = "analysis" | "court";

export type PanelLayout = {
  x: number;
  y: number;
  width: number;
  height: number;
  collapsed: boolean;
  alpha: number;
  presentation: PanelPresentation;
};

export type WorkspaceLayout = {
  version: 3;
  timelineMode: TimelineMode;
  analysisView: AnalysisView;
  analysisSide: AnalysisDockSide;
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

const detachedDefaults: Record<WorkspacePanelId, Omit<PanelLayout, "presentation">> = {
  timeline: { x: 0.025, y: 0.64, width: 0.70, height: 0.32, collapsed: false, alpha: 0.94 },
  analysis: { x: 0.735, y: 0.035, width: 0.24, height: 0.57, collapsed: false, alpha: 0.94 },
};

const modes: TimelineMode[] = ["rally", "stroke", "score", "commentary", "cheer"];
const views: AnalysisView[] = ["analysis", "court"];
const presentations: PanelPresentation[] = ["docked", "detached"];

function finite(value: unknown, fallback: number) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}
function clamp(value: unknown, fallback: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, finite(value, fallback)));
}
export function constrainAnalysisDockWidth(value: number) {
  return clamp(value, DEFAULT_ANALYSIS_DOCK_WIDTH, MIN_ANALYSIS_DOCK_WIDTH, MAX_ANALYSIS_DOCK_WIDTH);
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
    alpha: Math.min(0.99, Math.max(0.78, finite(panel.alpha, fallback.alpha))),
    presentation: presentations.includes(panel.presentation as PanelPresentation) ? panel.presentation! : fallback.presentation,
  };
}

export function defaultWorkspaceLayout(): WorkspaceLayout {
  return JSON.parse(JSON.stringify({
    version: 3,
    timelineMode: "rally",
    analysisView: "analysis",
    analysisSide: "right",
    analysisDockWidth: DEFAULT_ANALYSIS_DOCK_WIDTH,
    timelineDockHeight: DEFAULT_TIMELINE_DOCK_HEIGHT,
    panels: {
      timeline: { ...detachedDefaults.timeline, presentation: "docked" },
      analysis: { ...detachedDefaults.analysis, presentation: "docked" },
    },
    fullscreenPanels: {
      timeline: { x: 0.025, y: 0.66, width: 0.69, height: 0.25, collapsed: false, alpha: 0.94, presentation: "detached" },
      analysis: { ...detachedDefaults.analysis, presentation: "detached" },
    },
  })) as WorkspaceLayout;
}

export function parseWorkspaceLayout(raw: string | null): WorkspaceLayout {
  const fallback = defaultWorkspaceLayout();
  if (!raw) return fallback;
  try {
    const value = JSON.parse(raw) as Omit<Partial<WorkspaceLayout>, "version"> & { version?: number };
    if (value.version !== 1 && value.version !== 2 && value.version !== 3) return fallback;
    const migratedFromFloatingV1 = value.version === 1;
    const panelFallback = (id: WorkspacePanelId): PanelLayout => ({
      ...fallback.panels[id],
      presentation: migratedFromFloatingV1 ? "detached" : "docked",
    });
    return {
      version: 3,
      timelineMode: modes.includes(value.timelineMode as TimelineMode) ? value.timelineMode! : "rally",
      analysisView: views.includes(value.analysisView as AnalysisView) ? value.analysisView! : "analysis",
      analysisSide: value.analysisSide === "left" ? "left" : "right",
      analysisDockWidth: constrainAnalysisDockWidth(finite(value.analysisDockWidth, DEFAULT_ANALYSIS_DOCK_WIDTH)),
      timelineDockHeight: constrainTimelineDockHeight(finite(value.timelineDockHeight, DEFAULT_TIMELINE_DOCK_HEIGHT)),
      panels: {
        timeline: sanitizePanel(value.panels?.timeline, panelFallback("timeline")),
        analysis: sanitizePanel(value.panels?.analysis, panelFallback("analysis")),
      },
      fullscreenPanels: {
        timeline: sanitizePanel(value.fullscreenPanels?.timeline, fallback.fullscreenPanels.timeline),
        analysis: sanitizePanel(value.fullscreenPanels?.analysis, fallback.fullscreenPanels.analysis),
      },
    };
  } catch {
    return fallback;
  }
}

export function useWorkspaceLayout() {
  const stored = typeof localStorage === "undefined" ? null : localStorage.getItem(WORKSPACE_STORAGE_KEY);
  const layout = reactive(parseWorkspaceLayout(stored));

  function reset() {
    Object.assign(layout, defaultWorkspaceLayout());
  }
  function redockAll() {
    layout.panels.timeline.presentation = "docked";
    layout.panels.analysis.presentation = "docked";
  }

  if (typeof localStorage !== "undefined") {
    watch(layout, (value) => localStorage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(value)), { deep: true });
  }
  return { layout, reset, redockAll };
}
