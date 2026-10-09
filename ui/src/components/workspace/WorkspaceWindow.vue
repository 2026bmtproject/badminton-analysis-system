<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { MAX_PANEL_ALPHA, MIN_PANEL_ALPHA, type AnalysisDockSide, type PanelLayout, type WorkspacePanelId } from "../../state/workspaceLayout";
import {
  constrainPanel,
  type CollapsedSize,
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
  /** Fullscreen floats the window over the video; otherwise it is docked in the workspace grid. */
  fullscreen?: boolean;
  /** Moves the header below the body as a single toolbar, e.g. the fullscreen timeline that also hosts the player controls. */
  toolbarBottom?: boolean;
  dockSide?: AnalysisDockSide;
  dockSize?: number;
}>(), { passive: false, fullscreen: false, toolbarBottom: false, dockSide: "right", dockSize: 0 });
const emit = defineEmits<{
  change: [value: PanelLayout];
  activate: [];
  dockSize: [value: number];
  interaction: [value: boolean];
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
  capsule?: CollapsedSize;
  moved: boolean;
} | null = null;
/** A capsule header both drags and expands; the click that ends a drag must not expand it. */
let suppressHeaderClick = false;

const visiblePanel = computed(() => draft.value ?? props.panel);
const presentation = computed(() => props.fullscreen ? "detached" : "docked");
const analysisEdgeCollapsed = computed(
  () => props.panelId === "analysis" && !props.fullscreen && props.panel.collapsed,
);
/** A collapsed floating window shrinks to a movable capsule instead of a full-width strip. */
const capsule = computed(() => props.fullscreen && props.panel.collapsed);
const collapseIcon = computed(() => {
  if (props.panel.collapsed) return "chevron-up";
  if (props.panelId === "analysis" && !props.fullscreen) return props.dockSide === "left" ? "chevron-left" : "chevron-right";
  return "chevron-down";
});
const maxTransparency = Number((1 - MIN_PANEL_ALPHA).toFixed(2));
const transparency = computed(() => Number((1 - props.panel.alpha).toFixed(2)));
const style = computed(() => props.fullscreen ? ({
  left: `${visiblePanel.value.x * 100}%`,
  top: `${visiblePanel.value.y * 100}%`,
  width: capsule.value ? "auto" : `${visiblePanel.value.width * 100}%`,
  height: visiblePanel.value.collapsed ? "auto" : `${visiblePanel.value.height * 100}%`,
  "--workspace-panel-alpha": visiblePanel.value.alpha,
  zIndex: props.active ? 32 : 31,
}) : {});
watch([operation, menuOpen], () => emit("interaction", Boolean(operation.value || menuOpen.value)));

function containerSize() {
  const parent = root.value?.parentElement;
  return { width: Math.max(1, parent?.clientWidth ?? 1), height: Math.max(1, parent?.clientHeight ?? 1) };
}
function capsuleSize(): CollapsedSize | undefined {
  if (!capsule.value || !root.value) return undefined;
  return { width: root.value.offsetWidth, height: root.value.offsetHeight };
}
function update(change: Partial<PanelLayout>) {
  emit("change", { ...props.panel, ...change });
}
function floatingEnabled() {
  return props.fullscreen && !window.matchMedia("(max-width: 1100px)").matches;
}
function setTransparency(value: number) {
  update({ alpha: Math.min(MAX_PANEL_ALPHA, Math.max(MIN_PANEL_ALPHA, 1 - value)) });
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
function startDockResize(event: PointerEvent) {
  if (event.button !== 0 || props.panel.collapsed || props.fullscreen) return;
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
    capsule: capsuleSize(),
    moved: false,
  };
  operation.value = "drag";
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function drag(event: PointerEvent) {
  if (!gesture || operation.value !== "drag" || event.pointerId !== gesture.pointerId) return;
  const deltaX = event.clientX - gesture.startX;
  const deltaY = event.clientY - gesture.startY;
  if (Math.hypot(deltaX, deltaY) > 3) gesture.moved = true;
  draft.value = movePanel(gesture.panel, deltaX, deltaY, gesture.bounds, gesture.capsule);
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
    moved: false,
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
  const finalPanel = draft.value;
  if (operation.value === "drag" && gesture.moved) suppressHeaderClick = true;
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
  const next = constrainPanel(props.panel, containerSize(), capsuleSize());
  if (JSON.stringify(next) !== JSON.stringify(props.panel)) emit("change", next);
}
async function toggleCollapsed() {
  const bounds = containerSize();
  const collapsing = !props.panel.collapsed;
  // A bottom toolbar stays where the user clicked it: collapse and expand keep the bottom edge in place.
  const bottom = props.toolbarBottom && props.fullscreen && root.value
    ? (root.value.offsetTop + root.value.offsetHeight) / bounds.height
    : null;
  const next = { ...props.panel, collapsed: collapsing };
  if (bottom !== null && !collapsing) next.y = bottom - props.panel.height;
  emit("change", constrainPanel(next, bounds));
  if (bottom === null || !collapsing) return;
  await nextTick();
  const size = capsuleSize();
  if (size) emit("change", constrainPanel({ ...props.panel, y: bottom - size.height / bounds.height }, bounds, size));
}
function handleHeaderClick(event: MouseEvent) {
  if (suppressHeaderClick) { suppressHeaderClick = false; return; }
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
  clearChromeTimer();
  observer?.disconnect();
  window.removeEventListener("resize", handleResize);
});
</script>

<template>
  <section ref="root" class="workspace-window" :class="[`workspace-window--${presentation}`, `workspace-window--${panelId}`, `workspace-window--dock-${dockSide}`, { 'workspace-window--collapsed': panel.collapsed, 'workspace-window--capsule': capsule, 'workspace-window--toolbar-bottom': toolbarBottom, 'workspace-window--active': active, 'workspace-window--dragging': operation === 'drag', 'workspace-window--resizing': operation === 'resize', 'workspace-window--chrome-idle': chromeIdle } ]" :data-active="active" :data-presentation="presentation" :style="style" @pointerdown="emit('activate')" @pointerenter="handlePointerEnter" @pointerleave="handlePointerLeave" @focusin="handleFocusIn" @focusout="handleFocusOut">
    <div v-if="!fullscreen && !panel.collapsed" class="workspace-window__dock-resize" :aria-label="panelId === 'analysis' ? '調整分析寬度' : '調整時間軸高度'" role="separator" :aria-orientation="panelId === 'analysis' ? 'vertical' : 'horizontal'" data-no-window-drag @pointerdown.stop="startDockResize" @pointermove="resizeDock" @pointerup="endDockResize" @pointercancel="endDockResize" />
    <button v-if="analysisEdgeCollapsed" type="button" class="workspace-window__edge-tab" aria-label="展開分析" title="展開分析" @click="toggleCollapsed">
      <AppIcon :name="dockSide === 'left' ? 'chevron-right' : 'chevron-left'" :size="18" />
      <span>分析</span>
    </button>
    <header v-else class="workspace-window__header" @click="handleHeaderClick" @pointerdown="startDrag" @pointermove="drag" @pointerup="endGesture" @pointercancel="endGesture">
      <strong>{{ title }}</strong>
      <div class="workspace-window__header-slot"><slot name="header" /></div>
      <div class="workspace-window__actions">
        <WorkspacePopover v-if="fullscreen && !capsule" ref="menu" :label="`${title}視窗設定`" @open="handleMenuToggle">
          <template #trigger><AppIcon name="sliders" :size="17" /></template>
          <label><span>背景透明度 <output>{{ Math.round(transparency * 100) }}%</output></span><input aria-label="背景透明度" type="range" min="0" :max="maxTransparency" step="0.05" :value="transparency" @input="setTransparency(Number(($event.target as HTMLInputElement).value))" /></label>
          <button type="button" role="menuitem" @click="closeMenu(); emit('restore')">回復全螢幕預設位置</button>
        </WorkspacePopover>
        <button type="button" class="workspace-window__collapse" :aria-label="panel.collapsed ? `展開${title}` : `收合${title}`" :title="panel.collapsed ? `展開${title}` : `收合${title}`" @click="toggleCollapsed"><AppIcon :name="collapseIcon" :size="17" /></button>
      </div>
    </header>
    <div v-show="!panel.collapsed" class="workspace-window__body"><slot /></div>
    <div v-if="!panel.collapsed && fullscreen" class="workspace-window__resize-grip" aria-hidden="true" @pointerdown.stop="startResize" @pointermove="resize" @pointerup="endGesture" @pointercancel="endGesture" />
  </section>
</template>
