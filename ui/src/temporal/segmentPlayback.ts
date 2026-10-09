import { activeRallyAt } from "./activeContext";

type Segment = { start: number; end: number };

/** A playhead this close before a Segment start has landed there; media may report a seek a hair early. */
const LANDED_LEAD_SEC = 0.05;

/**
 * Where segments-only playback goes from `timeSec`: `null` keeps playing (inside a Segment), a number seeks
 * to the next Segment's start, and `"end"` means no Segment remains. Segments are sorted by start.
 */
export function segmentSkipTarget(segments: readonly Segment[], timeSec: number): number | "end" | null {
  if (!segments.length || !Number.isFinite(timeSec)) return null;
  if (activeRallyAt(segments, timeSec)) return null;
  const next = segments.find((segment) => segment.start > timeSec);
  if (!next) return "end";
  return next.start - timeSec <= LANDED_LEAD_SEC ? null : next.start;
}
