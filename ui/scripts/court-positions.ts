import type { MatchModel, CourtPositionUnavailableReason, PositionSource } from "../src/domain/models";
import { COURT_WIDTH_M, COURT_LENGTH_M, COURT_ADJACENT_MARGIN_M, distanceOutsideCourtM } from "../src/domain/courtPosition";

// Matches modules.common.court_geometry and modules.pose.select.to_court.
const MIN_ANKLE_CONFIDENCE = 0.3;
export const MAX_NEARBY_POSE_SECONDS = 0.12;
type Matrix = number[][];

export function invertHomography(value: unknown): Matrix | null {
  if (!Array.isArray(value) || value.length !== 3 ||
      !value.every((row) => Array.isArray(row) && row.length === 3 && row.every(Number.isFinite))) return null;
  const [[a,b,c],[d,e,f],[g,h,i]] = value as Matrix;
  const coefficients = [e*i-f*h,c*h-b*i,b*f-c*e,f*g-d*i,a*i-c*g,c*d-a*f,d*h-e*g,b*g-a*h,a*e-b*d];
  const determinant = a*coefficients[0]+b*coefficients[3]+c*coefficients[6];
  if (!Number.isFinite(determinant) || Math.abs(determinant) < 1e-12) return null;
  const inverse = [coefficients.slice(0,3), coefficients.slice(3,6), coefficients.slice(6,9)]
    .map((row) => row.map((v) => v / determinant));
  return inverse.flat().every(Number.isFinite) ? inverse : null;
}

export function imagePointToCourt(point: readonly [number, number], inverse: Matrix) {
  const projected = inverse.map((row) => row[0]*point[0] + row[1]*point[1] + row[2]);
  if (!Number.isFinite(projected[2]) || Math.abs(projected[2]) < 1e-9) return null;
  const x = projected[0] / projected[2] / COURT_WIDTH_M;
  const y = projected[1] / projected[2] / COURT_LENGTH_M;
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
}

type PoseFrame = { frame: number; segment_index: number; player: "top" | "bottom"; keypoints: unknown; bbox?: unknown };
function isPoseFrame(value: unknown): value is PoseFrame {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  return Number.isInteger(row.frame) && Number.isInteger(row.segment_index) &&
    (row.player === "top" || row.player === "bottom");
}
export function ankleGroundPoint(value: unknown) {
  if (!Array.isArray(value)) return null;
  const ankles = [value[15], value[16]].filter((ankle): ankle is number[] =>
    Array.isArray(ankle) && ankle.length >= 3 && ankle.every((n: unknown) => typeof n === "number" && Number.isFinite(n)) && ankle[2] >= MIN_ANKLE_CONFIDENCE);
  if (!ankles.length) return null;
  return {
    point: [ankles.reduce((sum, ankle) => sum + ankle[0], 0) / ankles.length,
      ankles.reduce((sum, ankle) => sum + ankle[1], 0) / ankles.length] as [number, number],
    source: (ankles.length === 2 ? "ankle_midpoint" : "single_ankle") as "ankle_midpoint" | "single_ankle",
    confidence: Math.min(...ankles.map((ankle) => ankle[2])) * (ankles.length === 2 ? 1 : 0.65),
  };
}

/** The selected pose record already belongs to one identified court-side player. */
export function bboxGroundPoint(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(Number.isFinite)) return null;
  const [x1, y1, x2, y2] = value as number[];
  return x2 > x1 && y2 > y1 ? [(x1 + x2) / 2, y2] : null;
}

/** Bounded same-player lookup; no interpolation and no cross-rally borrowing. */
export function deriveCourtPositions(model: MatchModel, rawCourt: unknown, rawPose: unknown): void {
  const court = rawCourt as { courts?: { homography?: unknown }[] } | undefined;
  const pose = rawPose as { frames?: unknown[] } | undefined;
  const inverse = invertHomography(court?.courts?.[0]?.homography);
  const frames = new Map<string, PoseFrame>();
  if (Array.isArray(pose?.frames)) for (const row of pose.frames) {
    if (isPoseFrame(row)) frames.set(`${row.segment_index}:${row.frame}:${row.player}`, row);
  }
  const maxOffset = model.fps ? Math.floor(model.fps * MAX_NEARBY_POSE_SECONDS + 1e-9) : 0;
  for (const rally of model.rallies) for (const hit of rally.hits ?? []) {
    let reason: CourtPositionUnavailableReason | null = null;
    const side = hit.hitterSide ??
      (hit.hitter && rally.identity ? (rally.identity.top === hit.hitter ? "top" : "bottom") : null);
    if (!side) reason = "HITTER_UNRESOLVED";
    else if (!inverse) reason = "COURT_TRANSFORM_UNAVAILABLE";
    else {
      const frameAt = (frame: number) => frames.get(`${rally.id}:${frame}:${side}`);
      const exact = frameAt(hit.frame);
      const nearby: PoseFrame[] = [];
      for (let offset = 1; offset <= maxOffset; offset++) {
        for (const frame of [hit.frame - offset, hit.frame + offset]) {
          const sample = frameAt(frame);
          if (sample) nearby.push(sample);
        }
      }
      let sawPose = !!exact || nearby.length > 0;
      let sawGround = false;
      function usePoint(point: readonly [number, number], source: PositionSource, sourceFrame: number, confidence?: number) {
        sawGround = true;
        const coordinate = imagePointToCourt(point, inverse!);
        if (!coordinate || distanceOutsideCourtM(coordinate.x, coordinate.y) > COURT_ADJACENT_MARGIN_M) return false;
        hit.courtPosition = {
          ...coordinate, coordinateSpace: "court_normalized_v1", source, sourceFrame,
          ...(confidence === undefined ? {} : { confidence }),
        };
        hit.positionQuality = source === "ankle_midpoint" ? "measured" : "estimated";
        hit.positionSource = source;
        return true;
      }
      const exactAnkle = ankleGroundPoint(exact?.keypoints);
      if (exactAnkle?.source === "ankle_midpoint")
        usePoint(exactAnkle.point, "ankle_midpoint", hit.frame, exactAnkle.confidence);
      if (!hit.courtPosition) for (const sample of nearby) {
        const ground = ankleGroundPoint(sample.keypoints);
        if (ground?.source === "ankle_midpoint" &&
            usePoint(ground.point, "nearby_frame_ankle_midpoint", sample.frame, ground.confidence)) break;
      }
      if (!hit.courtPosition && exactAnkle?.source === "single_ankle")
        usePoint(exactAnkle.point, "single_ankle", hit.frame, exactAnkle.confidence);
      if (!hit.courtPosition) for (const sample of nearby) {
        const ground = ankleGroundPoint(sample.keypoints);
        if (ground?.source === "single_ankle" &&
            usePoint(ground.point, "single_ankle", sample.frame, ground.confidence)) break;
      }
      if (!hit.courtPosition) {
        const proxy = bboxGroundPoint(exact?.bbox);
        if (proxy) usePoint(proxy, "bbox_bottom_center", hit.frame);
      }
      if (!hit.courtPosition) reason = !sawPose ? "POSE_UNAVAILABLE" : !sawGround ? "ANKLES_UNAVAILABLE" : "OUT_OF_COURT";
    }
    if (reason) {
      hit.positionQuality = "unresolved";
      hit.positionUnavailableReason = reason;
    }
  }
}
