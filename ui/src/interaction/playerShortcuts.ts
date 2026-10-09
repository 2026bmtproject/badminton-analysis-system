export type PlayerShortcutAction =
  | "toggle"
  | "seek-back-10"
  | "seek-forward-10"
  | "seek-back-5"
  | "seek-forward-5"
  | "toggle-mute"
  | "toggle-fullscreen"
  | "rate-down"
  | "rate-up"
  | "toggle-segments-only";

/** Seeks keep stepping while their key is held; every other action fires once per press. */
const HOLD_REPEATS: ReadonlySet<PlayerShortcutAction> = new Set([
  "seek-back-10",
  "seek-forward-10",
  "seek-back-5",
  "seek-forward-5",
]);

export function repeatsWhileHeld(action: PlayerShortcutAction): boolean {
  return HOLD_REPEATS.has(action);
}

export function playerShortcutAction(input: {
  code: string;
  key: string;
  shiftKey: boolean;
}): PlayerShortcutAction | null {
  if (
    input.shiftKey &&
    (input.code === "ArrowLeft" || input.code === "ArrowRight")
  )
    return null;
  if (input.code === "Space" || input.code === "KeyK") return "toggle";
  if (input.code === "KeyJ") return "seek-back-10";
  if (input.code === "KeyL") return "seek-forward-10";
  if (input.code === "ArrowLeft") return "seek-back-5";
  if (input.code === "ArrowRight") return "seek-forward-5";
  if (input.code === "KeyM") return "toggle-mute";
  if (input.code === "KeyF") return "toggle-fullscreen";
  if (input.code === "KeyS") return "toggle-segments-only";
  if (input.key === "<" || (input.shiftKey && input.code === "Comma"))
    return "rate-down";
  if (input.key === ">" || (input.shiftKey && input.code === "Period"))
    return "rate-up";
  return null;
}
