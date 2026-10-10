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
  startedAt?: string; finishedAt?: string;
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

export function isActiveTask(task: PipelineTask): boolean {
  return task.status === "queued" || task.status === "running";
}

/** Seconds as m:ss, or h:mm:ss past an hour. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const [h, m, s] = [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60];
  const pad = (value: number) => String(value).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** Wall-clock run time; null before it starts. */
export function taskElapsed(task: PipelineTask, now: number): string | null {
  if (!task.startedAt) return null;
  const end = task.finishedAt ? Date.parse(task.finishedAt) : now;
  return formatDuration((end - Date.parse(task.startedAt)) / 1000);
}

/** A stage's own run time in seconds; null for stages that never started (queued or reused). */
export function stageSeconds(stage: TaskStage, now: number): number | null {
  if (!stage.startedAt) return null;
  const end = stage.finishedAt ? Date.parse(stage.finishedAt) : now;
  return Math.max(0, (end - Date.parse(stage.startedAt)) / 1000);
}
export function stageElapsed(stage: TaskStage, now: number): string | null {
  const seconds = stageSeconds(stage, now);
  return seconds === null ? null : formatDuration(seconds);
}

/** Stages this task actually ran, in plan order; reused results are not part of the work. */
export function ranStages(task: PipelineTask): [string, TaskStage][] {
  return Object.entries(task.stageStates).filter(([, stage]) => stage.status !== "skipped");
}

/** Whole-task completion over the stages it ran: finished ones count fully, the running one by its fraction. */
export function taskProgress(task: PipelineTask) {
  const states = ranStages(task).map(([, stage]) => stage);
  const done = states.filter(stage => stage.status === "succeeded").length;
  const current = task.currentStage ? task.stageStates[task.currentStage] : undefined;
  const partial = current?.status === "running" ? current.progress ?? 0 : 0;
  return { done, total: states.length, fraction: states.length ? Math.min(1, (done + partial) / states.length) : 0 };
}

/** "擊球偵測、球種辨識 +2": what a task worked on, short enough for one row. */
export function ranStagesSummary(task: PipelineTask, label: (name: string) => string, shown = 2): string {
  const names = ranStages(task).map(([name]) => label(name));
  const rest = names.length - shown;
  return names.slice(0, shown).join("、") + (rest > 0 ? ` +${rest}` : "");
}

/** Task log lines start with an ISO UTC stamp; the page shows it as local wall-clock time. */
export function formatLogLine(line: string): string {
  const match = /^(\S+?T\S+)\s(.*)$/.exec(line);
  const time = match ? new Date(match[1]) : null;
  if (!match || !time || Number.isNaN(time.getTime())) return line;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(time.getHours())}:${pad(time.getMinutes())}:${pad(time.getSeconds())}  ${match[2]}`;
}

/** "今天 03:09", "昨天 22:14", "10/08 14:02", or with the year once it differs. */
export function formatTaskTime(iso: string, now: number): string {
  const date = new Date(iso);
  const pad = (value: number) => String(value).padStart(2, "0");
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const today = new Date(now);
  const dayStart = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((dayStart(today) - dayStart(date)) / 86_400_000);
  if (days === 0) return `今天 ${time}`;
  if (days === 1) return `昨天 ${time}`;
  const day = `${pad(date.getMonth() + 1)}/${pad(date.getDate())}`;
  return date.getFullYear() === today.getFullYear() ? `${day} ${time}` : `${date.getFullYear()}/${day} ${time}`;
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
/** Starts the same request again against a fresh plan, since the old plan's ID is stale by now. */
export async function retryTask(task: PipelineTask) {
  return startPipeline(await previewPipeline(task.matchId, task.plan.requestedStages, task.plan.mode));
}
export const LOG_PAGE_SIZE = 100;
export function getPipelineLogs(id: string, offset: number) {
  return request<{ lines: string[]; nextOffset: number }>(`tasks/${id}/logs?offset=${offset}&limit=${LOG_PAGE_SIZE}`);
}
