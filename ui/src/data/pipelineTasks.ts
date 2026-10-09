export type PipelineStage = {
  name: string; description: string; dependencies: string[];
  optionalDependencies: string[]; usesGemini: boolean;
};
export type StagePlan = {
  name: string; action: "run" | "skip"; reason: string; stale: string[];
  unknown: boolean; status: string;
};
export type PipelinePlan = {
  planId: string; matchId: string; mode: "continue" | "rerun-selected";
  requestedStages: string[]; requiredStages: string[]; stages: StagePlan[];
  affectedOutsideScope: string[]; includesGemini: boolean;
};
export type TaskStage = {
  status: string; progress: number | null; reason: string; error?: string;
};
export type PipelineTask = {
  id: string; matchId: string; status: "queued" | "running" | "succeeded" | "failed" | "interrupted";
  plan: PipelinePlan; stageStates: Record<string, TaskStage>; currentStage: string | null;
  createdAt: string; startedAt: string | null; finishedAt: string | null;
  error: string | null; exitCode: number | null;
};
export type LocalAnalysisMatch = {
  id: string; completedStages: string[]; hasSegments: boolean;
  analysisStatus: "completed" | "partial" | "unanalysed";
  latestTask: PipelineTask | null;
};

export function taskStatusLabel(status: string): string {
  return ({ queued: "排隊中", running: "分析中", succeeded: "分析完成",
    failed: "分析失敗", interrupted: "執行中斷", skipped: "沿用既有結果" } as Record<string, string>)[status] ?? status;
}

/** Wall-clock run time as m:ss, or h:mm:ss once a task passes an hour; null before it starts. */
export function taskElapsed(task: PipelineTask, now: number): string | null {
  if (!task.startedAt) return null;
  const end = task.finishedAt ? Date.parse(task.finishedAt) : now;
  const total = Math.max(0, Math.floor((end - Date.parse(task.startedAt)) / 1000));
  const [h, m, s] = [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60];
  const pad = (value: number) => String(value).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** Whole-task completion: finished stages count fully, the running one by its reported fraction. */
export function taskProgress(task: PipelineTask) {
  const states = Object.values(task.stageStates);
  const done = states.filter(stage => stage.status === "succeeded" || stage.status === "skipped").length;
  const current = task.currentStage ? task.stageStates[task.currentStage] : undefined;
  const partial = current?.status === "running" ? current.progress ?? 0 : 0;
  return { done, total: states.length, fraction: states.length ? (done + partial) / states.length : 0 };
}

/** The planner's reasons are English diagnostics; the panel shows them in plain Chinese. */
export function planReasonLabel(row: StagePlan): string {
  if (row.reason === "selected for rerun") return "指定重跑";
  if (row.reason.startsWith("upstream will change")) return "前面的階段會更新";
  if (row.reason === "missing or unfinished") return "尚未完成";
  if (row.reason.startsWith("stale")) return "輸入資料已變更";
  if (row.reason === "invalid artifact") return "結果檔無法讀取";
  if (row.reason.startsWith("unknown inputs")) return "沿用（舊結果無法確認輸入）";
  return "沿用既有結果";
}

async function request<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`/api/pipeline/${path}`, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : { cache: "no-store" });
  const data = await response.json() as T & { error?: string };
  if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`);
  return data;
}

export async function listPipelineMatches() {
  return (await request<{ matches: LocalAnalysisMatch[] }>("matches")).matches;
}
export async function listPipelineTasks() {
  return (await request<{ tasks: PipelineTask[] }>("tasks")).tasks;
}
export async function listPipelineStages() {
  return (await request<{ stages: PipelineStage[] }>("stages")).stages;
}
export function previewPipeline(matchId: string, stages: string[], mode: PipelinePlan["mode"]) {
  return request<PipelinePlan>("plan", { matchId, stages, mode });
}
export function startPipeline(plan: PipelinePlan) {
  return request<PipelineTask>("tasks", {
    matchId: plan.matchId, stages: plan.requestedStages, mode: plan.mode, planId: plan.planId,
  });
}
export function getPipelineTask(id: string) { return request<PipelineTask>(`tasks/${id}`); }
export const LOG_PAGE_SIZE = 100;
export function getPipelineLogs(id: string, offset: number) {
  return request<{ lines: string[]; nextOffset: number }>(`tasks/${id}/logs?offset=${offset}&limit=${LOG_PAGE_SIZE}`);
}
