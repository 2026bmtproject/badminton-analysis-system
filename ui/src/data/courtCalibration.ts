export type CourtPoint = [number, number];
export type CourtReview = {
  matchId: string; revision: string; width: number; height: number; image: string;
  corners: CourtPoint[]; points: CourtPoint[]; lines: [number, number][];
  confirmed: boolean; detectionFailed: boolean; legacyPreview: boolean;
  coordinateUnknown: boolean; saveBlocked: boolean;
};

async function request<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`/api/pipeline/court${path}`, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : { cache: "no-store" });
  const data = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`);
  return data;
}

export function loadCourt(matchId: string) {
  return request<CourtReview>(`?matchId=${encodeURIComponent(matchId)}`);
}
export function previewCourt(matchId: string, revision: string, corners: CourtPoint[]) {
  return request<{ points: CourtPoint[] }>("/preview", { matchId, revision, corners });
}
export function saveCourt(matchId: string, revision: string, corners: CourtPoint[]) {
  return request<{ staleStages: string[]; unknownStages: string[] }>("/save", { matchId, revision, corners });
}

export type ViewBox = { x: number; y: number; width: number; height: number };

/**
 * Maps a pointer position to image pixels through the SVG viewBox. The default
 * `xMidYMid meet` scales the viewBox uniformly and centres it, so a height-capped
 * element letterboxes instead of stretching. Results are clamped to the image.
 */
export function clientToCourt(clientX: number, clientY: number, rect: DOMRect, view: ViewBox, width: number, height: number): CourtPoint {
  const scale = Math.min(rect.width / view.width, rect.height / view.height);
  const left = rect.left + (rect.width - view.width * scale) / 2;
  const top = rect.top + (rect.height - view.height * scale) / 2;
  return [Math.max(0, Math.min(width, view.x + (clientX - left) / scale)),
    Math.max(0, Math.min(height, view.y + (clientY - top) / scale))];
}
