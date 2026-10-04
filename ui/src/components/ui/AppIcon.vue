<script setup lang="ts">
defineProps<{
  name:
    | "play"
    | "pause"
    | "arrow-left"
    | "chevron-left"
    | "chevron-right"
    | "volume"
    | "volume-muted"
    | "fullscreen"
    | "fullscreen-exit"
    | "sliders";
  size?: number;
}>();
</script>

<template>
  <svg
    class="app-icon"
    :class="`app-icon--${name}`"
    :width="size ?? 18"
    :height="size ?? 18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path
      v-if="name === 'play'"
      d="m8 5 11 7-11 7Z"
      fill="currentColor"
      stroke="none"
    />
    <template v-else-if="name === 'pause'">
      <path d="M9 5v14M15 5v14" />
    </template>
    <template v-else-if="name === 'arrow-left'">
      <path d="m12 19-7-7 7-7M5 12h14" />
    </template>
    <path v-else-if="name === 'chevron-left'" d="m15 18-6-6 6-6" />
    <path v-else-if="name === 'chevron-right'" d="m9 18 6-6-6-6" />
    <template v-else-if="name === 'volume' || name === 'volume-muted'">
      <path d="M11 5 6 9H3v6h3l5 4Z" />
      <path
        v-if="name === 'volume'"
        d="M15 9.5a4 4 0 0 1 0 5M18 7a7 7 0 0 1 0 10"
      />
      <path v-else d="m16 10 5 5m0-5-5 5" />
    </template>
    <template v-else-if="name === 'fullscreen'">
      <path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" />
    </template>
    <template v-else-if="name === 'fullscreen-exit'">
      <path d="M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5" />
    </template>
    <template v-else>
      <path d="M4 7h10M18 7h2M4 12h2M10 12h10M4 17h8M16 17h4" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="8" cy="12" r="2" />
      <circle cx="14" cy="17" r="2" />
    </template>
  </svg>
</template>
