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
export function getPipelineLogs(id: string, offset: number) {
  return request<{ lines: string[]; nextOffset: number }>(`tasks/${id}/logs?offset=${offset}&limit=100`);
}
