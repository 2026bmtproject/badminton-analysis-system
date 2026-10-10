import { ref } from "vue";
import type { DesktopStatus } from "../desktop";

/*
 * One copy of the desktop host's status for the status bar and the settings page. The status bar reads it on load
 * and when the window regains focus; the settings page refreshes it while open.
 */
const status = ref<DesktopStatus | null>(null);
const error = ref("");

async function refresh() {
  if (!window.badmintonDesktop) return;
  try { status.value = await window.badmintonDesktop.status(); error.value = ""; }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "無法讀取桌面狀態"; }
}

export function useDesktopStatus() {
  return { status, error, refresh, available: Boolean(window.badmintonDesktop) };
}
