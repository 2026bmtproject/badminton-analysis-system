import type { PipelineStage, PipelineTask } from "./pipelineTasks";

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
/**
 * `rerunStages` already read the old court in the running task, whose worker reruns them; `staleStages` lie outside
 * it and wait for the next analysis.
 */
export type CourtSaveResult = { staleStages: string[]; unknownStages: string[]; rerunStages?: string[] };
export function saveCourt(matchId: string, revision: string, corners: CourtPoint[]) {
  return request<CourtSaveResult>("/save", { matchId, revision, corners });
}

export const COURT_STAGE = "court_detection";

/** Stages that read the court, directly or through another stage, in the order the service lists them. */
export function courtReaders(stages: readonly PipelineStage[]): string[] {
  const found = new Set<string>();
  for (let grew = true; grew;) {
    grew = false;
    for (const stage of stages) {
      if (found.has(stage.name)) continue;
      if ([...stage.dependencies, ...stage.optionalDependencies].some((name) => name === COURT_STAGE || found.has(name))) {
        found.add(stage.name);
        grew = true;
      }
    }
  }
  return stages.map((stage) => stage.name).filter((name) => found.has(name));
}

/**
 * What a running task asks of the user once its court is detected: a look, while the stages that do not read the
 * court go on. `ran` already used this court and would rerun after a repair; `upcoming` will simply read the repair.
 * Null when there is nothing to check.
 */
export function courtCheck(task: PipelineTask | null, readers: readonly string[]): { ran: string[]; upcoming: string[] } | null {
  if (!task || !courtAwaitsCheck(task)) return null;
  const inTask = task.plan.requiredStages.filter((name) => readers.includes(name) && task.stageStates[name]);
  const status = (name: string) => task.stageStates[name]!.status;
  return {
    ran: inTask.filter((name) => status(name) === "succeeded" || status(name) === "running"),
    upcoming: inTask.filter((name) => status(name) === "queued"),
  };
}

/** A running task has just detected its court, which the user should look at while the rest runs. */
export function courtAwaitsCheck(task: PipelineTask): boolean {
  return (task.status === "queued" || task.status === "running") && task.segmentIndex === undefined &&
    task.stageStates[COURT_STAGE]?.status === "succeeded";
}

/** Why the court cannot be saved during this task, or null when it can: its court stage has yet to finish. */
export function courtSaveLock(task: PipelineTask | null): string | null {
  if (!task || (task.status !== "queued" && task.status !== "running")) return null;
  if (task.segmentIndex !== undefined) return "賽評產生中，暫時不能保存場地。";
  const court = task.stageStates[COURT_STAGE]?.status;
  return court === "queued" || court === "running" ? "球場偵測完成後就能檢查與調整場地。" : null;
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
