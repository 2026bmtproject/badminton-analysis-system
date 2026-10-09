<script setup lang="ts">
defineProps<{ description: string; kind: string }>();
</script>

<template>
  <div
    class="timeline-lane"
    :class="`timeline-lane--${kind}`"
    :data-kind="kind"
  >
    <!-- One narrow column for every lane, so tracks line up; the mode selector
         already names the lane, so only a lane with a legend fills it. -->
    <span class="timeline-lane-label"><slot name="label" /></span>
    <div class="timeline-lane-track" role="group" :aria-label="description">
      <slot />
    </div>
  </div>
</template>
