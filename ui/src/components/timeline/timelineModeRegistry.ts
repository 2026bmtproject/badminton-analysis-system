import type { MatchCapabilities } from "../../domain/models";
import type { TimelineMode } from "../../state/workspaceLayout";

export type TimelineModeRegistration = {
  id: TimelineMode;
  label: string;
  capability: keyof MatchCapabilities | null;
};

/**
 * Presentation registry only. Every entry maps to a renderer backed by an
 * existing MatchModel capability; adding a label here never creates data.
 */
export const timelineModeRegistry: readonly TimelineModeRegistration[] = [
  { id: "rally", label: "片段", capability: null },
  { id: "stroke", label: "擊球", capability: "stroke" },
  { id: "score", label: "比分", capability: "score" },
  { id: "cheer", label: "歡呼", capability: "cheer" },
];

export function availableTimelineModes(capabilities: MatchCapabilities) {
  return timelineModeRegistry.filter(
    (mode) => mode.capability === null || capabilities[mode.capability],
  );
}
