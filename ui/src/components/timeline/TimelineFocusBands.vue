<script setup lang="ts">
import type { FocusBand } from "../../temporal/timelineNavigation";

defineProps<{ bands: FocusBand[] }>();
</script>

<template>
  <!-- First in its lane so every mark paints over it. A stretched 0–100 viewBox
       keeps the band in SVG units, gliding by subpixels with the marks; the
       edges stay one pixel wide through non-scaling strokes. -->
  <svg v-if="bands.length" class="timeline-focus-bands" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
    <template v-for="band in bands" :key="`focus-${band.state}`">
      <rect class="timeline-focus" :data-state="band.state" :x="band.left" y="0" :width="band.width" height="100" />
      <line class="timeline-focus-edge" :data-state="band.state" :x1="band.left" :x2="band.left" y1="0" y2="100" />
      <line v-if="band.state === 'selected'" class="timeline-focus-edge" data-state="selected" :x1="band.left + band.width" :x2="band.left + band.width" y1="0" y2="100" />
    </template>
  </svg>
</template>
