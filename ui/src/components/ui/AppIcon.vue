<script setup lang="ts">
defineProps<{
  name:
    | "play"
    | "pause"
    | "arrow-left"
    | "chevron-left"
    | "chevron-right"
    | "chevron-up"
    | "chevron-down"
    | "volume"
    | "volume-muted"
    | "fullscreen"
    | "fullscreen-exit"
    | "dock"
    | "float"
    | "sliders"
    | "library"
    | "tasks"
    | "settings"
    | "search"
    | "video"
    | "more"
    | "refresh"
    | "court"
    | "segments"
    | "layers"
    | "folder"
    | "alert";
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
    <path v-else-if="name === 'chevron-up'" d="m18 15-6-6-6 6" />
    <path v-else-if="name === 'chevron-down'" d="m6 9 6 6 6-6" />
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
    <!-- The docked workbench: video, with the timeline below and the analysis beside it. -->
    <template v-else-if="name === 'dock'"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 15h12M15 4v16" /></template>
    <!-- A window floating over the video, clear of its edges. -->
    <template v-else-if="name === 'float'"><rect x="3" y="4" width="18" height="16" rx="2" /><rect x="6" y="12" width="10" height="5" rx="1" /></template>
    <template v-else-if="name === 'library'"><path d="M4 5h16v14H4zM4 10h16M9 5v14" /></template>
    <template v-else-if="name === 'tasks'"><path d="M8 5h12M8 12h12M8 19h12M3 5h1M3 12h1M3 19h1" /></template>
    <template v-else-if="name === 'settings'"><circle cx="12" cy="12" r="3" /><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" /></template>
    <template v-else-if="name === 'search'"><circle cx="11" cy="11" r="7" /><path d="m16 16 5 5" /></template>
    <template v-else-if="name === 'video'"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m10 9 5 3-5 3z" /></template>
    <template v-else-if="name === 'more'"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></template>
    <template v-else-if="name === 'alert'"><circle cx="12" cy="12" r="9" /><path d="M12 7.5v5.5M12 16.5h.01" /></template>
    <template v-else-if="name === 'refresh'"><path d="M20 7v5h-5M4 17v-5h5M5 9a8 8 0 0 1 14-2l1 5M4 12l1 5a8 8 0 0 0 14-2"/></template>
    <template v-else-if="name === 'segments'"><path d="M2 8h5v8H2zM17 8h5v8h-5z"/><path d="M9.5 12h5m-2-2.5 2.5 2.5-2.5 2.5"/></template>
    <template v-else-if="name === 'layers'"><path d="m12 3 9 5-9 5-9-5Z"/><path d="m3 12.5 9 5 9-5"/><path d="m3 17 9 5 9-5"/></template>
    <template v-else-if="name === 'folder'"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/></template>
    <template v-else-if="name === 'court'"><rect x="3" y="3" width="18" height="18" rx="1"/><path d="M12 3v18M3 12h18M7 8h10M7 16h10"/></template>
    <template v-else>
      <path d="M4 7h10M18 7h2M4 12h2M10 12h10M4 17h8M16 17h4" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="8" cy="12" r="2" />
      <circle cx="14" cy="17" r="2" />
    </template>
  </svg>
</template>
