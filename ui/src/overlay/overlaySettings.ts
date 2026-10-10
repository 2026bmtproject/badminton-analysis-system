import type { MatchModel } from "../domain/models";

export type OverlayLayerId = "court" | "pose" | "players" | "shuttle" | "trail" | "hit" | "stroke";

/** Menu order; the digit key toggles the layer from the keyboard. */
export const OVERLAY_LAYERS: readonly { id: OverlayLayerId; label: string; key: string; parent?: OverlayLayerId }[] = [
  { id: "court", label: "球場線", key: "1" },
  { id: "pose", label: "骨架", key: "2" },
  { id: "players", label: "選手標籤", key: "3" },
  { id: "shuttle", label: "羽球", key: "4" },
  { id: "trail", label: "球路拖尾", key: "5", parent: "shuttle" },
  { id: "hit", label: "擊球閃圈", key: "6" },
  { id: "stroke", label: "球種標籤", key: "7" },
];

/** Every shuttle tracking method at once. */
export const BOTH_METHODS = "both";

export type OverlaySettings = {
  /** The master switch; layers keep their own choice while it is off. */
  enabled: boolean;
  layers: Record<OverlayLayerId, boolean>;
  /** A tracking method name, or BOTH_METHODS. */
  shuttleMethod: string;
};

export function defaultOverlaySettings(): OverlaySettings {
  return {
    enabled: false,
    // The stroke label repeats the Analysis window's stroke list, so it waits to be asked for.
    layers: { court: true, pose: true, players: true, shuttle: true, trail: true, hit: true, stroke: false },
    shuttleMethod: BOTH_METHODS,
  };
}

export function sanitizeOverlaySettings(value: unknown): OverlaySettings {
  const fallback = defaultOverlaySettings();
  const raw = value && typeof value === "object" ? (value as Partial<OverlaySettings>) : {};
  const layers = raw.layers && typeof raw.layers === "object" ? raw.layers : {};
  return {
    enabled: raw.enabled === true,
    layers: Object.fromEntries(OVERLAY_LAYERS.map(({ id }) => {
      const choice = (layers as Partial<Record<OverlayLayerId, unknown>>)[id];
      return [id, typeof choice === "boolean" ? choice : fallback.layers[id]];
    })) as Record<OverlayLayerId, boolean>,
    shuttleMethod: typeof raw.shuttleMethod === "string" && raw.shuttleMethod ? raw.shuttleMethod : BOTH_METHODS,
  };
}

/** Why each layer cannot be drawn for this match, or null when it can. */
export type OverlayAvailability = Record<OverlayLayerId, string | null>;

export function overlayAvailability(model: Pick<MatchModel, "capabilities" | "overlay" | "fps" | "layoutOnly">): OverlayAvailability {
  const { capabilities, overlay } = model;
  if (model.layoutOnly || !model.fps) {
    const reason = model.layoutOnly ? "版面示範沒有影片" : "缺少影格率資料";
    return Object.fromEntries(OVERLAY_LAYERS.map(({ id }) => [id, reason])) as OverlayAvailability;
  }
  const needsFiles = (capable: boolean, missing: string) =>
    !capable ? missing : overlay ? null : "重新匯入比賽後才有疊圖資料";
  const court = needsFiles(capabilities.court, "沒有可用的球場偵測結果");
  const pose = needsFiles(capabilities.pose, "沒有可用的姿態結果");
  const shuttle = needsFiles(capabilities.shuttle && Boolean(overlay?.methods.length ?? true), "沒有可用的羽球軌跡");
  const stroke = capabilities.stroke ? null : "沒有可用的擊球結果";
  return { court, pose, players: pose, shuttle, trail: shuttle, hit: stroke, stroke };
}

/** The layers that actually draw: switched on, available, and with their parent layer on. */
export function activeOverlayLayers(settings: OverlaySettings, availability: OverlayAvailability): Record<OverlayLayerId, boolean> {
  return Object.fromEntries(OVERLAY_LAYERS.map(({ id, parent }) => [id,
    settings.enabled && settings.layers[id] && availability[id] === null &&
    (!parent || settings.layers[parent])])) as Record<OverlayLayerId, boolean>;
}

/** Methods to draw; an unknown stored choice falls back to all of them. */
export function shownShuttleMethods(settings: OverlaySettings, methods: readonly string[]): string[] {
  return methods.includes(settings.shuttleMethod) ? [settings.shuttleMethod] : [...methods];
}

export function toggleOverlay(settings: OverlaySettings) {
  settings.enabled = !settings.enabled;
}

/** Switching a layer on also switches the overlay on: asking for a layer means wanting to see it. */
export function toggleOverlayLayer(settings: OverlaySettings, id: OverlayLayerId) {
  settings.layers[id] = !settings.layers[id];
  if (settings.layers[id]) settings.enabled = true;
}

export type OverlayShortcut = { kind: "overlay" } | { kind: "layer"; id: OverlayLayerId };

export function overlayShortcut(input: { code: string; shiftKey: boolean }): OverlayShortcut | null {
  if (input.shiftKey) return null;
  if (input.code === "KeyO") return { kind: "overlay" };
  const digit = /^(?:Digit|Numpad)([1-9])$/.exec(input.code)?.[1];
  const layer = digit ? OVERLAY_LAYERS.find((item) => item.key === digit) : undefined;
  return layer ? { kind: "layer", id: layer.id } : null;
}
