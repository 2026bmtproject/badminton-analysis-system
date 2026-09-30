<script setup lang="ts">
defineProps<{ label?: string; description: string; kind: string }>();
</script>

<template>
  <div
    class="timeline-lane"
    :class="`timeline-lane--${kind}`"
    :data-kind="kind"
  >
    <span v-if="label" class="timeline-lane-label">{{ label }}</span>
    <span v-else class="timeline-lane-label" aria-hidden="true" />
    <div class="timeline-lane-track" role="group" :aria-label="description">
      <slot />
    </div>
  </div>
</template>
