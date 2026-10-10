import { ref } from "vue";

export const APP_NAME = "Badminton Review";

/** The open Match's display title, set by MatchShell while it is mounted; the window title leads with it. */
export const matchTitle = ref<string | null>(null);

const PAGE_TITLES: Record<string, string> = {
  matches: "比賽庫",
  tasks: "任務",
  settings: "設定",
  "match-analysis": "分析設定",
};

/** The context the window title names before the app name, as VS Code names the open file. */
export function windowContext(routeName: unknown, match: string | null): string | null {
  if (routeName === "match-review") return match;
  return typeof routeName === "string" ? PAGE_TITLES[routeName] ?? null : null;
}

export function windowTitle(context: string | null): string {
  return context ? `${context} — ${APP_NAME}` : APP_NAME;
}
