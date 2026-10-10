import type { OverlayPoint } from "./overlayChunk";
import type { OverlayMark, OverlayTone } from "./overlayScene";

/** The picture inside the canvas, in CSS pixels, and CSS pixels per video pixel. */
export type OverlayView = { x: number; y: number; width: number; height: number; scale: number };

export type OverlayPalette = {
  court: string;
  neutral: string;
  a: string;
  b: string;
  hit: string;
  labelBackground: string;
  font: string;
  /** Colours for shuttle tracking methods, by name; other methods take `fallbackMethods` in turn. */
  methods: Record<string, string>;
  fallbackMethods: readonly string[];
};

/** Theme colours come from CSS custom properties, so the overlay follows the token file. */
export function readOverlayPalette(element: Element): OverlayPalette {
  const style = getComputedStyle(element);
  const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  return {
    court: token("--overlay-court", "#f2d649"),
    neutral: token("--overlay-neutral", "#e6e6e6"),
    a: token("--color-player-a", "#4f93cf"),
    b: token("--color-player-b", "#cf6f5e"),
    hit: token("--overlay-hit", "#ff4d4d"),
    labelBackground: token("--overlay-label-bg", "rgb(20 20 20 / 78%)"),
    font: token("--font-interface", "sans-serif"),
    methods: { inpaint: token("--overlay-shuttle-inpaint", "#3fd0e0"), viterbi: token("--overlay-shuttle-viterbi", "#e05cd6") },
    fallbackMethods: ["#9be564", "#ffffff"],
  };
}

function colour(palette: OverlayPalette, tone: OverlayTone, methodOrder: readonly string[]) {
  if (!tone.startsWith("method:")) return palette[tone as Exclude<OverlayTone, `method:${string}`>];
  const method = tone.slice("method:".length);
  return palette.methods[method] ??
    palette.fallbackMethods[Math.max(0, methodOrder.indexOf(method)) % palette.fallbackMethods.length]!;
}

/**
 * Draws one frame's marks onto a context already scaled to CSS pixels. Everything is clipped to the
 * picture, so letterbox bars stay clean.
 */
export function drawOverlay(
  context: CanvasRenderingContext2D,
  marks: readonly OverlayMark[],
  view: OverlayView,
  palette: OverlayPalette,
  methodOrder: readonly string[] = [],
) {
  const at = (point: OverlayPoint): [number, number] => [view.x + point[0] * view.scale, view.y + point[1] * view.scale];
  context.save();
  context.beginPath();
  context.rect(view.x, view.y, view.width, view.height);
  context.clip();
  context.lineCap = "round";
  context.lineJoin = "round";
  for (const mark of marks) {
    const tint = colour(palette, mark.tone, methodOrder);
    context.globalAlpha = 1;
    if (mark.kind === "line") {
      context.globalAlpha = mark.alpha ?? 1;
      context.strokeStyle = tint;
      context.lineWidth = mark.width;
      context.beginPath();
      mark.points.forEach((point, index) => {
        const [x, y] = at(point);
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      if (mark.closed) context.closePath();
      context.stroke();
    } else if (mark.kind === "circle") {
      const [x, y] = at(mark.center);
      context.beginPath();
      context.arc(x, y, mark.radius, 0, Math.PI * 2);
      if (mark.width === "fill") {
        context.fillStyle = tint;
        context.fill();
      } else {
        context.strokeStyle = tint;
        context.lineWidth = mark.width;
        context.stroke();
      }
    } else if (mark.kind === "border") {
      context.strokeStyle = tint;
      context.lineWidth = mark.width;
      context.strokeRect(view.x + mark.width / 2, view.y + mark.width / 2, view.width - mark.width, view.height - mark.width);
    } else {
      drawLabel(context, mark, tint, view, palette, at);
    }
  }
  context.restore();
}

function drawLabel(
  context: CanvasRenderingContext2D,
  mark: Extract<OverlayMark, { kind: "label" }>,
  tint: string,
  view: OverlayView,
  palette: OverlayPalette,
  at: (point: OverlayPoint) => [number, number],
) {
  const size = mark.align === "center" ? 16 : 14;
  context.font = `600 ${size}px ${palette.font}`;
  const padX = 6, padY = 3;
  const width = context.measureText(mark.text).width + padX * 2;
  const height = size + padY * 2;
  const [anchorX, anchorY] = mark.at === "video-bottom" ? [view.x + view.width / 2, view.y + view.height] : at(mark.at);
  const left = Math.min(Math.max(mark.align === "center" ? anchorX - width / 2 : anchorX, view.x + 2), view.x + view.width - width - 2);
  const top = Math.min(Math.max(anchorY - mark.lift - height, view.y + 2), view.y + view.height - height - 2);
  context.fillStyle = palette.labelBackground;
  context.beginPath();
  context.roundRect(left, top, width, height, 3);
  context.fill();
  context.fillStyle = tint;
  context.textBaseline = "middle";
  context.textAlign = "left";
  context.fillText(mark.text, left + padX, top + height / 2 + 0.5);
}
