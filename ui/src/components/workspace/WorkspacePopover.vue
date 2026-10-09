<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { placeWorkspacePopover } from "../../presentation/workspacePopover";

const props = withDefaults(defineProps<{
  label: string;
  minWidth?: number;
}>(), { minWidth: 200 });
const emit = defineEmits<{ open: [value: boolean] }>();
const trigger = ref<HTMLButtonElement | null>(null);
const menu = ref<HTMLElement | null>(null);
const open = ref(false);
const teleportTarget = ref<HTMLElement | string>("body");
const position = ref({ left: 8, top: 8 });
let observer: ResizeObserver | undefined;

function place() {
  if (!trigger.value || !menu.value) return;
  const anchor = trigger.value.getBoundingClientRect();
  const surface = menu.value.getBoundingClientRect();
  position.value = placeWorkspacePopover(
    anchor,
    surface,
    { width: window.innerWidth, height: window.innerHeight },
    props.minWidth,
  );
}
function setOpen(value: boolean) {
  open.value = value;
  emit("open", value);
  if (value) void nextTick(() => {
    place();
    if (menu.value) observer?.observe(menu.value);
  });
  else observer?.disconnect();
}
function toggle() { setOpen(!open.value); }
function close() { setOpen(false); }
function outside(event: PointerEvent) {
  const target = event.target;
  if (!(target instanceof Node)) return;
  if (trigger.value?.contains(target) || menu.value?.contains(target)) return;
  close();
}
function keyboard(event: KeyboardEvent) {
  if (event.key !== "Escape" || !open.value) return;
  close();
  trigger.value?.focus();
}
function menuClick(event: MouseEvent) {
  if ((event.target as Element).closest("button,[role='menuitem']")) close();
}
function syncTeleportTarget() {
  teleportTarget.value = document.fullscreenElement instanceof HTMLElement ? document.fullscreenElement : "body";
  if (open.value) void nextTick(place);
}
onMounted(() => {
  syncTeleportTarget();
  observer = new ResizeObserver(place);
  window.addEventListener("pointerdown", outside);
  window.addEventListener("keydown", keyboard);
  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, true);
  document.addEventListener("fullscreenchange", syncTeleportTarget);
});
onBeforeUnmount(() => {
  observer?.disconnect();
  window.removeEventListener("pointerdown", outside);
  window.removeEventListener("keydown", keyboard);
  window.removeEventListener("resize", place);
  window.removeEventListener("scroll", place, true);
  document.removeEventListener("fullscreenchange", syncTeleportTarget);
});
defineExpose({ close, toggle });
</script>

<template>
  <span class="workspace-popover-anchor">
    <button ref="trigger" type="button" class="workspace-popover__trigger" :aria-label="label" :title="label" aria-haspopup="menu" :aria-expanded="open" @click.stop="toggle">
      <slot name="trigger" />
    </button>
    <Teleport :to="teleportTarget">
      <div v-if="open" ref="menu" class="workspace-popover" role="menu" :aria-label="label" :style="{ left: position.left + 'px', top: position.top + 'px', minWidth: minWidth + 'px' }" @click="menuClick">
        <slot />
      </div>
    </Teleport>
  </span>
</template>
