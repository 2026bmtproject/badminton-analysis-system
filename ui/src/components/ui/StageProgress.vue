<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ status: string; progress: number | null; label?: string }>();

/** Finished stages fill the bar even when the worker never reported a fraction. */
const fraction = computed(() => {
  if (props.status === "succeeded" || props.status === "skipped") return 1;
  return props.progress == null ? null : Math.min(1, Math.max(0, props.progress));
});
const indeterminate = computed(() => props.status === "running" && fraction.value === null);
</script>

<template>
  <span class="stage-progress" :data-status="status" :data-indeterminate="indeterminate || undefined"
    role="progressbar" :aria-label="label" aria-valuemin="0" aria-valuemax="100"
    :aria-valuenow="fraction === null ? undefined : Math.round(fraction * 100)">
    <span class="stage-progress-fill" :style="{ width: indeterminate ? undefined : `${(fraction ?? 0) * 100}%` }" />
  </span>
</template>
