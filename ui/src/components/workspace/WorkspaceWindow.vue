<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { AnalysisDockSide, PanelLayout, WorkspacePanelId } from "../../state/workspaceLayout";
import {
  constrainPanel,
  fullscreenSnapCandidate,
  movePanel,
  resizePanel,
  type WorkspaceBounds,
} from "../../state/workspaceGeometry";
import AppIcon from "../ui/AppIcon.vue";
import WorkspacePopover from "./WorkspacePopover.vue";

const props = withDefaults(defineProps<{
  title: string;
  panel: PanelLayout;
  panelId: WorkspacePanelId;
  active: boolean;
  passive?: boolean;
  fullscreen?: boolean;
  dockSide?: AnalysisDockSide;
  dockSize?: number;
}>(), { passive: false, fullscreen: false, dockSide: "right", dockSize: 0 });
const emit = defineEmits<{
  change: [value: PanelLayout];
  activate: [];
  presentation: [value: "docked" | "detached"];
  dockSide: [value: AnalysisDockSide];
  dockSize: [value: number];
  interaction: [value: boolean];
  snapDrag: [value: { dragging: boolean; near: boolean }];
  restore: [];
}>();
const root = ref<HTMLElement | null>(null);
const menu = ref<InstanceType<typeof WorkspacePopover> | null>(null);
const draft = ref<PanelLayout | null>(null);
const operation = ref<"drag" | "resize" | null>(null);
const chromeIdle = ref(false);
const focusWithin = ref(false);
const pointerWithin = ref(false);
const menuOpen = ref(false);
let chromeTimer: number | undefined;
let observer: ResizeObserver | undefined;
let dockGesture: { pointerId: number; start: number; size: number } | null = null;
let gesture: {
  pointerId: number;
  startX: number;
  startY: number;
  panel: PanelLayout;
  bounds: WorkspaceBounds;
} | null = null;

const visiblePanel = computed(() => draft.value ?? props.panel);
const analysisEdgeCollapsed = computed(
  () =>
    props.panelId === "analysis" &&
    props.panel.presentation === "docked" &&
    props.panel.collapsed,
);
const style = computed(() => props.panel.presentation === "detached" ? ({
  left: `${visiblePanel.value.x * 100}%`,
  top: `${visiblePanel.value.y * 100}%`,
  width: `${visiblePanel.value.width * 100}%`,
  height: visiblePanel.value.collapsed ? "auto" : `${visiblePanel.value.height * 100}%`,
  "--workspace-panel-alpha": visiblePanel.value.alpha,
  zIndex: props.active ? 32 : 31,
}) : ({ "--workspace-panel-alpha": visiblePanel.value.alpha }));
watch([operation, menuOpen], () => emit("interaction", Boolean(operation.value || menuOpen.value)));

function containerSize() {
  const parent = root.value?.parentElement;
  return { width: Math.max(1, parent?.clientWidth ?? 1), height: Math.max(1, parent?.clientHeight ?? 1) };
}
function update(change: Partial<PanelLayout>) {
  emit("change", { ...props.panel, ...change });
}
function floatingEnabled() {
  return props.panel.presentation === "detached" && !window.matchMedia("(max-width: 1100px)").matches;
}
function clearChromeTimer() {
  if (chromeTimer !== undefined) window.clearTimeout(chromeTimer);
  chromeTimer = undefined;
}
function scheduleChromeIdle() {
  clearChromeTimer();
  if (!props.passive || focusWithin.value || pointerWithin.value || menuOpen.value || operation.value) {
    chromeIdle.value = false;
    return;
  }
  chromeTimer = window.setTimeout(() => { chromeIdle.value = true; }, 1400);
}
function revealChrome() {
  chromeIdle.value = false;
  scheduleChromeIdle();
}
function handleFocusIn() {
  focusWithin.value = true;
  chromeIdle.value = false;
  clearChromeTimer();
  emit("activate");
}
function handleFocusOut(event: FocusEvent) {
  if (event.relatedTarget instanceof Node && root.value?.contains(event.relatedTarget)) return;
  focusWithin.value = false;
  scheduleChromeIdle();
}
function handlePointerEnter() {
  pointerWithin.value = true;
  chromeIdle.value = false;
  clearChromeTimer();
}
function handlePointerLeave() {
  pointerWithin.value = false;
  scheduleChromeIdle();
}
function handleMenuToggle(value: boolean) {
  menuOpen.value = value;
  revealChrome();
}
function closeMenu() {
  menu.value?.close();
  menuOpen.value = false;
}
function changePresentation(value: "docked" | "detached") {
  closeMenu();
  emit("presentation", value);
}
function dockAnalysis(side: AnalysisDockSide) {
  closeMenu();
  emit("dockSide", side);
  emit("presentation", "docked");
}
function startDockResize(event: PointerEvent) {
  if (event.button !== 0 || props.panel.collapsed || props.panel.presentation !== "docked") return;
  emit("activate");
  revealChrome();
  dockGesture = {
    pointerId: event.pointerId,
    start: props.panelId === "analysis" ? event.clientX : event.clientY,
    size: props.dockSize,
  };
  operation.value = "resize";
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function resizeDock(event: PointerEvent) {
  if (!dockGesture || event.pointerId !== dockGesture.pointerId) return;
  const position = props.panelId === "analysis" ? event.clientX : event.clientY;
  const delta = position - dockGesture.start;
  const direction = props.panelId === "timeline" || props.dockSide === "right" ? -1 : 1;
  emit("dockSize", dockGesture.size + delta * direction);
}
function endDockResize(event: PointerEvent) {
  if (!dockGesture || event.pointerId !== dockGesture.pointerId) return;
  dockGesture = null;
  operation.value = null;
  scheduleChromeIdle();
}
function startDrag(event: PointerEvent) {
  if (
    event.button !== 0 ||
    !floatingEnabled() ||
    (event.target as Element).closest(
      "button,select,input,label,a,details,summary,[data-no-window-drag]",
    )
  )
    return;
  emit("activate");
  revealChrome();
  gesture = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    panel: { ...props.panel },
    bounds: containerSize(),
  };
  operation.value = "drag";
  if (props.fullscreen) emit("snapDrag", { dragging: true, near: false });
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function drag(event: PointerEvent) {
  if (!gesture || operation.value !== "drag" || event.pointerId !== gesture.pointerId) return;
  draft.value = movePanel(
    gesture.panel,
    event.clientX - gesture.startX,
    event.clientY - gesture.startY,
    gesture.bounds,
  );
  if (props.fullscreen && draft.value) emit("snapDrag", {
    dragging: true,
    near: fullscreenSnapCandidate(props.panelId, draft.value, gesture.bounds) !== null,
  });
}
function startResize(event: PointerEvent) {
  if (event.button !== 0 || props.panel.collapsed || !floatingEnabled()) return;
  emit("activate");
  revealChrome();
  gesture = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    panel: { ...props.panel },
    bounds: containerSize(),
  };
  operation.value = "resize";
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function resize(event: PointerEvent) {
  if (!gesture || operation.value !== "resize" || event.pointerId !== gesture.pointerId) return;
  draft.value = resizePanel(
    gesture.panel,
    event.clientX - gesture.startX,
    event.clientY - gesture.startY,
    gesture.bounds,
  );
}
function endGesture(event: PointerEvent) {
  if (!gesture || event.pointerId !== gesture.pointerId) return;
  const finalPanel = draft.value && operation.value === "drag" && props.fullscreen
    ? fullscreenSnapCandidate(props.panelId, draft.value, gesture.bounds) ?? draft.value
    : draft.value;
  if (operation.value === "drag" && props.fullscreen) emit("snapDrag", { dragging: false, near: false });
  gesture = null;
  operation.value = null;
  draft.value = null;
  if (finalPanel) emit("change", finalPanel);
  scheduleChromeIdle();
}
function constrain() {
  if (!root.value || !floatingEnabled() || operation.value) return;
  // Fullscreen transitions can briefly report a zero-sized parent. Do not
  // persist a position clamped against that transient measurement.
  if (!root.value.parentElement || root.value.parentElement.clientWidth < 220 || root.value.parentElement.clientHeight < 96) return;
  const next = constrainPanel(props.panel, containerSize());
  if (JSON.stringify(next) !== JSON.stringify(props.panel)) emit("change", next);
}
function toggleCollapsed() {
  const next = constrainPanel(
    { ...props.panel, collapsed: !props.panel.collapsed },
    containerSize(),
  );
  emit("change", next);
}
function handleHeaderClick(event: MouseEvent) {
  if (!props.panel.collapsed || (event.target as Element).closest("button,select,input,label,a")) return;
  toggleCollapsed();
}
function handleResize() {
  requestAnimationFrame(constrain);
}
onMounted(() => {
  observer = new ResizeObserver(handleResize);
  if (root.value?.parentElement) observer.observe(root.value.parentElement);
  window.addEventListener("resize", handleResize);
  constrain();
  scheduleChromeIdle();
});
watch(() => props.passive, scheduleChromeIdle);
onBeforeUnmount(() => {
  emit("interaction", false);
  emit("snapDrag", { dragging: false, near: false });
  clearChromeTimer();
  observer?.disconnect();
  window.removeEventListener("resize", handleResize);
});
</script>

<template>
  <section ref="root" class="workspace-window" :class="[`workspace-window--${panel.presentation}`, `workspace-window--${panelId}`, `workspace-window--dock-${dockSide}`, { 'workspace-window--collapsed': panel.collapsed, 'workspace-window--active': active, 'workspace-window--dragging': operation === 'drag', 'workspace-window--resizing': operation === 'resize', 'workspace-window--chrome-idle': chromeIdle } ]" :data-active="active" :data-presentation="panel.presentation" :style="style" @pointerdown="emit('activate')" @pointerenter="handlePointerEnter" @pointerleave="handlePointerLeave" @focusin="handleFocusIn" @focusout="handleFocusOut">
    <div v-if="panel.presentation === 'docked' && !panel.collapsed" class="workspace-window__dock-resize" :aria-label="panelId === 'analysis' ? '調整分析寬度' : '調整時間軸高度'" role="separator" :aria-orientation="panelId === 'analysis' ? 'vertical' : 'horizontal'" data-no-window-drag @pointerdown.stop="startDockResize" @pointermove="resizeDock" @pointerup="endDockResize" @pointercancel="endDockResize" />
    <button v-if="analysisEdgeCollapsed" type="button" class="workspace-window__edge-tab" aria-label="展開分析" title="展開分析" @click="toggleCollapsed">
      <AppIcon :name="dockSide === 'left' ? 'chevron-right' : 'chevron-left'" :size="18" />
      <span>分析</span>
    </button>
    <header v-else class="workspace-window__header" @click="handleHeaderClick" @pointerdown="startDrag" @pointermove="drag" @pointerup="endGesture" @pointercancel="endGesture">
      <strong>{{ title }}</strong>
      <div class="workspace-window__header-slot"><slot name="header" /></div>
      <div class="workspace-window__actions">
        <WorkspacePopover ref="menu" :label="`${title}視窗設定`" @open="handleMenuToggle">
          <template #trigger><AppIcon name="sliders" :size="17" /></template>
          <label>背景透明度<input aria-label="背景透明度" type="range" min="0.01" max="0.22" step="0.01" :value="Number((1 - panel.alpha).toFixed(2))" @input="update({ alpha: 1 - Number(($event.target as HTMLInputElement).value) })" /></label>
          <button v-if="fullscreen && panel.presentation === 'detached'" type="button" role="menuitem" @click="closeMenu(); emit('restore')">回復全螢幕預設位置</button>
          <button v-if="panel.presentation === 'docked'" type="button" role="menuitem" @click="changePresentation('detached')">脫離工作區</button>
          <template v-else>
            <button v-if="panelId === 'timeline'" type="button" role="menuitem" @click="changePresentation('docked')">停靠底部</button>
            <button v-else type="button" role="menuitem" @click="dockAnalysis('left')">停靠左側</button>
            <button v-if="panelId === 'analysis'" type="button" role="menuitem" @click="dockAnalysis('right')">停靠右側</button>
          </template>
        </WorkspacePopover>
        <button type="button" :aria-label="panel.collapsed ? `展開${title}` : `收合${title}`" @click="toggleCollapsed">{{ panel.collapsed ? "＋" : "—" }}</button>
      </div>
    </header>
    <div v-if="!panel.collapsed || (panel.presentation === 'docked' && panelId === 'timeline')" class="workspace-window__body"><slot /></div>
    <div v-if="!panel.collapsed && panel.presentation === 'detached'" class="workspace-window__resize-grip" aria-hidden="true" @pointerdown.stop="startResize" @pointermove="resize" @pointerup="endGesture" @pointercancel="endGesture" />
  </section>
</template>
