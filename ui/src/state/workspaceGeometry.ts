import type { PanelLayout } from "./workspaceLayout";

export type WorkspaceBounds = { width: number; height: number };

export const PANEL_MIN_WIDTH_PX = 220;
export const PANEL_MIN_HEIGHT_PX = 96;
export const PANEL_MAX_WIDTH_RATIO = 0.96;
export const PANEL_MAX_HEIGHT_RATIO = 0.92;
export const PANEL_HEADER_HEIGHT_PX = 42;

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
