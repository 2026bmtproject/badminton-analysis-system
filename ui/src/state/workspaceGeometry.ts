import { TIMELINE_FIT_HEIGHT_PX } from "../presentation/workspaceDensity";
import { defaultWorkspaceLayout, type PanelLayout, type WorkspacePanelId } from "./workspaceLayout";

export type WorkspaceBounds = { width: number; height: number };

export const PANEL_MIN_WIDTH_PX = 220;
export const PANEL_MIN_HEIGHT_PX = 96;
export const PANEL_MAX_WIDTH_RATIO = 0.96;
export const PANEL_MAX_HEIGHT_RATIO = 0.92;
export const PANEL_HEADER_HEIGHT_PX = 42;
export const FULLSCREEN_SNAP_DISTANCE_PX = 72;
/** Timeline lanes have fixed pixel heights, so the floating timeline uses the same fitted height as the dock. */
export const FULLSCREEN_TIMELINE_HEIGHT_PX = TIMELINE_FIT_HEIGHT_PX;

const clamp = (value: number, minimum: number, maximum: number) =>
  Math.min(maximum, Math.max(minimum, value));

function normalizedConstraints(bounds: WorkspaceBounds) {
  const width = Math.max(1, bounds.width);
  const height = Math.max(1, bounds.height);
  return {
    minWidth: Math.min(PANEL_MAX_WIDTH_RATIO, PANEL_MIN_WIDTH_PX / width),
    minHeight: Math.min(PANEL_MAX_HEIGHT_RATIO, PANEL_MIN_HEIGHT_PX / height),
    collapsedHeight: Math.min(1, PANEL_HEADER_HEIGHT_PX / height),
  };
}

export function constrainPanel(
  panel: PanelLayout,
  bounds: WorkspaceBounds,
): PanelLayout {
  const limits = normalizedConstraints(bounds);
  const width = clamp(panel.width, limits.minWidth, PANEL_MAX_WIDTH_RATIO);
  const height = clamp(panel.height, limits.minHeight, PANEL_MAX_HEIGHT_RATIO);
  const visibleHeight = panel.collapsed ? limits.collapsedHeight : height;
  return {
    ...panel,
    width,
    height,
    x: clamp(panel.x, 0, Math.max(0, 1 - width)),
    y: clamp(panel.y, 0, Math.max(0, 1 - visibleHeight)),
  };
}

export function movePanel(
  panel: PanelLayout,
  deltaX: number,
  deltaY: number,
  bounds: WorkspaceBounds,
) {
  return constrainPanel(
    {
      ...panel,
      x: panel.x + deltaX / Math.max(1, bounds.width),
      y: panel.y + deltaY / Math.max(1, bounds.height),
    },
    bounds,
  );
}

export function fullscreenHomePanel(
  id: WorkspacePanelId,
  panel: PanelLayout,
  bounds: WorkspaceBounds,
): PanelLayout {
  const home = defaultWorkspaceLayout().fullscreenPanels[id];
  return constrainPanel({ ...panel, x: home.x, y: home.y }, bounds);
}

/**
 * An untouched fullscreen timeline floats at a fixed pixel height anchored to
 * its default bottom edge, so it neither scrolls on short screens nor towers on
 * tall ones. Once the user resizes it, their stored size wins.
 */
export function fittedFullscreenTimeline(panel: PanelLayout, boundsHeight: number): PanelLayout {
  const home = defaultWorkspaceLayout().fullscreenPanels.timeline;
  if (panel.height !== home.height) return panel;
  const height = Math.min(PANEL_MAX_HEIGHT_RATIO, FULLSCREEN_TIMELINE_HEIGHT_PX / Math.max(1, boundsHeight));
  const y = panel.y === home.y ? Math.max(0, home.y + home.height - height) : panel.y;
  return { ...panel, height, y };
}

export function fullscreenSnapCandidate(
  id: WorkspacePanelId,
  panel: PanelLayout,
  bounds: WorkspaceBounds,
): PanelLayout | null {
  const home = fullscreenHomePanel(id, panel, bounds);
  const distance = Math.hypot(
    (panel.x - home.x) * bounds.width,
    (panel.y - home.y) * bounds.height,
  );
  return distance <= FULLSCREEN_SNAP_DISTANCE_PX ? home : null;
}

export function resizePanel(
  panel: PanelLayout,
  deltaX: number,
  deltaY: number,
  bounds: WorkspaceBounds,
) {
  return constrainPanel(
    {
      ...panel,
      width: panel.width + deltaX / Math.max(1, bounds.width),
      height: panel.height + deltaY / Math.max(1, bounds.height),
    },
    bounds,
  );
}
