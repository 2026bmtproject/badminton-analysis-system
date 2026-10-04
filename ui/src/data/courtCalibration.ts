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

export function clientToCourt(clientX: number, clientY: number, rect: DOMRect, width: number, height: number): CourtPoint {
  return [Math.max(0, Math.min(width, (clientX - rect.left) * width / rect.width)),
    Math.max(0, Math.min(height, (clientY - rect.top) * height / rect.height))];
}
