export type RectLike = { left: number; right: number; top: number; bottom: number };

export function placeWorkspacePopover(
  anchor: RectLike,
  surface: { width: number; height: number },
  viewport: { width: number; height: number },
  minimumWidth: number,
  gutter = 8,
) {
  const width = Math.min(viewport.width - gutter * 2, Math.max(minimumWidth, surface.width));
  let left = anchor.left;
  let top = anchor.bottom + gutter;
  if (left + width > viewport.width - gutter) left = anchor.right - width;
  if (top + surface.height > viewport.height - gutter) top = anchor.top - surface.height - gutter;
  return {
    left: Math.max(gutter, Math.min(left, viewport.width - width - gutter)),
    top: Math.max(gutter, Math.min(top, viewport.height - surface.height - gutter)),
    width,
  };
}
