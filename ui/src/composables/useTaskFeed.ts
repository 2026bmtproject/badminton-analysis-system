import { computed, onMounted, onUnmounted, ref } from "vue";
import { isActiveTask, listPipelineTasks, type PipelineTask } from "../data/pipelineTasks";

/*
 * One poller for every view that watches tasks (the nav badge, the tasks page, the analysis panel), so they never
 * disagree and never double the request rate. Its failures double as the service's connection state. It polls fast only while a task runs. It also owns refreshing a
 * match's Review once its task succeeds, wherever the user happens to be at that moment.
 */
const tasks = ref<PipelineTask[]>([]);
const error = ref("");
const loaded = ref(false);
const now = ref(Date.now());
/** Polls failed in a row; one miss (a service still starting, a slow restart) is not yet "offline". */
const failures = ref(0);
const SEEN_KEY = "tasks.seenFailure";
const seenFailure = ref(readSeen());
export type ReviewSync = { state: "publishing" | "done" | "failed"; error?: string };
/** Review refresh state by task ID; each succeeded task is published at most once unless a retry is asked for. */
const reviewSync = ref<Record<string, ReviewSync>>({});
/** Bumps whenever a publish lands, for views that list which matches have a Review. */
const reviewVersion = ref(0);
const lastStatus = new Map<string, PipelineTask["status"]>();
let primed = false;
let users = 0;
let generation = 0;
let timer: ReturnType<typeof setTimeout> | undefined;

function readSeen(): string | null {
  try { return localStorage.getItem(SEEN_KEY); } catch { return null; }
}

const active = computed(() => tasks.value.find(isActiveTask) ?? null);
const offline = computed(() => failures.value >= 2);
const newest = computed(() => tasks.value[0] ?? null);
/** The newest task failed and nobody has opened the tasks page since. */
const unseenFailure = computed(() => {
  const task = newest.value;
  return Boolean(task && (task.status === "failed" || task.status === "interrupted") && task.id !== seenFailure.value);
});

/** True when the Review was already imported after this task finished, e.g. by an earlier session. */
async function reviewIsCurrent(task: PipelineTask) {
  const { loadCatalog, loadMatch } = await import("../data/matchRepository");
  try {
    const entry = (await loadCatalog()).find(row => row.id === `match:${task.matchId}`);
    if (!entry || !task.finishedAt) return false;
    const importedAt = (await loadMatch(entry, { fresh: true })).source?.importedAt;
    return Boolean(importedAt && Date.parse(importedAt) >= Date.parse(task.finishedAt));
  } catch {
    return false; // An unreadable old cache should not prevent a fresh export.
  }
}

/** Publishes a succeeded task's results as its match's Review; `verify` skips the work when it is already current. */
async function syncReview(task: PipelineTask, { verify = true, retry = false } = {}) {
  if (task.status !== "succeeded") return;
  const current = reviewSync.value[task.id];
  if (current && !(retry && current.state === "failed")) return;
  reviewSync.value[task.id] = { state: "publishing" };
  try {
    if (verify && await reviewIsCurrent(task)) { reviewSync.value[task.id] = { state: "done" }; return; }
    const { importLocalMatch } = await import("../data/matchRepository");
    await importLocalMatch(task.matchId);
    reviewSync.value[task.id] = { state: "done" };
    reviewVersion.value++;
  } catch (cause) {
    reviewSync.value[task.id] = { state: "failed", error: cause instanceof Error ? cause.message : "回看更新失敗" };
  }
}

async function refresh() {
  try {
    tasks.value = await listPipelineTasks(); error.value = "";
    // A task that ran or even appeared since the last poll finished in this session; one already done on the
    // first poll is left to the analysis panel, which checks whether its Review is current.
    for (const task of tasks.value) {
      const before = lastStatus.get(task.id);
      const fresh = before === "queued" || before === "running" || (primed && before === undefined);
      if (task.status === "succeeded" && fresh) void syncReview(task, { verify: false });
      lastStatus.set(task.id, task.status);
    }
    primed = true;
    failures.value = 0;
  }
  catch (cause) {
    error.value = cause instanceof Error ? cause.message : "無法讀取任務";
    failures.value++;
  }
  finally { loaded.value = true; now.value = Date.now(); }
}

function schedule(run: number) {
  timer = setTimeout(async () => {
    await refresh();
    if (run === generation) schedule(run);
  }, active.value ? 1500 : 4000);
}

function acknowledgeFailure() {
  const task = newest.value;
  if (!task || !unseenFailure.value) return;
  seenFailure.value = task.id;
  try { localStorage.setItem(SEEN_KEY, task.id); } catch { /* the badge just reappears next session */ }
}

export function useTaskFeed() {
  onMounted(() => {
    if (users++ > 0) return;
    const run = ++generation;
    void refresh().then(() => { if (run === generation) schedule(run); });
  });
  onUnmounted(() => {
    if (--users > 0) return;
    generation++;
    clearTimeout(timer);
  });
  return { tasks, error, loaded, now, active, offline, unseenFailure, refresh, acknowledgeFailure, reviewSync, reviewVersion, syncReview };
}
