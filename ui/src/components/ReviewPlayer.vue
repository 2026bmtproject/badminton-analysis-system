<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { RallyModel, StrokeModel } from "../domain/models";
import { usePlayer } from "../composables/usePlayer";
import { formatPreciseTime, formatTime, playerName } from "../format";
import type { PlayerShortcutAction } from "../interaction/playerShortcuts";
import {
  edgeLabelSide,
  percentOfDuration,
} from "../interaction/temporalInteraction";
import AppIcon from "./ui/AppIcon.vue";
type PlayerContext = {
  label: string;
  score: string | null;
  meta: string;
};
const props = withDefaults(defineProps<{
  src: string;
  context: PlayerContext;
  activeRally: RallyModel | null;
  activeStroke: StrokeModel | null;
  timelineExpanded?: boolean;
}>(), { timelineExpanded: false });
const emit = defineEmits<{ time: [value: number]; playing: [value: boolean] }>();
const root = ref<HTMLElement | null>(null);
const video = ref<HTMLVideoElement | null>(null);
const progressVisual = ref<HTMLElement | null>(null);
const progressWidth = ref(1);
const mediaAspectRatio = ref("16 / 9");
const inspectionTime = ref<number | null>(null);
const muted = ref(false);
const fullscreen = ref(false);
const playbackRates = [0.5, 0.75, 1, 1.5, 2] as const;
const RALLY_LOCATOR_EDGE_PADDING_PX = 18;
let progressObserver: ResizeObserver | undefined;
const { state, seek, metadata, sync, toggle, setRate, failed } = usePlayer(
  video,
  () => props.src,
);
const progressPercent = computed(() =>
  state.duration > 0
    ? Math.min(100, Math.max(0, (state.time / state.duration) * 100))
    : 0,
);
const rallyStartPercent = computed(() =>
  props.activeRally
    ? percentOfDuration(props.activeRally.start, state.duration)
    : 0,
);
const rallyEndPercent = computed(() =>
  props.activeRally
    ? percentOfDuration(props.activeRally.end, state.duration)
    : 0,
);
const rallyMidpointPercent = computed(
  () => (rallyStartPercent.value + rallyEndPercent.value) / 2,
);
const rallyIndex = computed(() =>
  props.activeRally
    ? String(props.activeRally.id + 1).padStart(3, "0")
    : "",
);
const rallyLocatorSide = computed(() =>
  edgeLabelSide(
    (rallyMidpointPercent.value / 100) * progressWidth.value,
    progressWidth.value,
    RALLY_LOCATOR_EDGE_PADDING_PX,
  ),
);
const activeStrokePercent = computed(() =>
  props.activeStroke
    ? percentOfDuration(props.activeStroke.time, state.duration)
    : 0,
);
const inspectionPercent = computed(() =>
  inspectionTime.value === null
    ? 0
    : percentOfDuration(inspectionTime.value, state.duration),
);
watch(
  () => state.time,
  (t) => emit("time", t),
  { flush: "sync" },
);
watch(
  () => state.paused,
  (paused) => emit("playing", !paused),
  { flush: "sync", immediate: true },
);
function media(event: Event) {
  if (!(event.currentTarget instanceof HTMLVideoElement)) {
    throw new Error("Expected a video event source");
  }
  return event.currentTarget;
}
function updateMetadata(event: Event) {
  const element = media(event);
  muted.value = element.muted;
  if (element.videoWidth > 0 && element.videoHeight > 0) {
    mediaAspectRatio.value = `${element.videoWidth} / ${element.videoHeight}`;
  }
  metadata(element);
}
function syncMute(event: Event) {
  muted.value = media(event).muted;
}
function scrub(event: Event) {
  if (event.currentTarget instanceof HTMLInputElement) {
    seek(Number(event.currentTarget.value));
  }
}
function changeRate(event: Event) {
  if (event.currentTarget instanceof HTMLSelectElement) {
    setRate(Number(event.currentTarget.value));
  }
}
function seekBy(seconds: number) {
  seek(state.time + seconds);
}
function toggleMute() {
  if (!video.value) return;
  video.value.muted = !video.value.muted;
  muted.value = video.value.muted;
}
async function toggleFullscreen() {
  if (!root.value) return;
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await root.value.requestFullscreen();
  } catch {
    // The browser may deny fullscreen without a fresh user gesture.
  }
}
function stepRate(direction: -1 | 1) {
  const current = playbackRates.reduce(
    (closest, rate, index) =>
      Math.abs(rate - state.rate) <
      Math.abs(playbackRates[closest] - state.rate)
        ? index
        : closest,
    0,
  );
  const next = Math.min(
    playbackRates.length - 1,
    Math.max(0, current + direction),
  );
  setRate(playbackRates[next]);
}
function handleShortcut(action: PlayerShortcutAction) {
  if (action === "toggle") void toggle();
  else if (action === "seek-back-10") seekBy(-10);
  else if (action === "seek-forward-10") seekBy(10);
  else if (action === "seek-back-5") seekBy(-5);
  else if (action === "seek-forward-5") seekBy(5);
  else if (action === "toggle-mute") toggleMute();
  else if (action === "toggle-fullscreen") void toggleFullscreen();
  else if (action === "rate-down") stepRate(-1);
  else if (action === "rate-up") stepRate(1);
}
function setInspectionTime(value: number | null) {
  inspectionTime.value = value;
}
function syncFullscreen() {
  fullscreen.value = document.fullscreenElement === root.value;
}
watch(
  () => props.src,
  () => {
    mediaAspectRatio.value = "16 / 9";
    muted.value = false;
    inspectionTime.value = null;
  },
  { flush: "sync" },
);
watch(
  video,
  (element) => {
    muted.value = element?.muted ?? false;
    if (element?.videoWidth && element.videoHeight) {
      mediaAspectRatio.value = `${element.videoWidth} / ${element.videoHeight}`;
    }
  },
  { flush: "post" },
);
onMounted(() => {
  document.addEventListener("fullscreenchange", syncFullscreen);
  progressObserver = new ResizeObserver(([entry]) => {
    progressWidth.value = Math.max(1, entry.contentRect.width);
  });
  if (progressVisual.value) progressObserver.observe(progressVisual.value);
});
onBeforeUnmount(() => {
  progressObserver?.disconnect();
  document.removeEventListener("fullscreenchange", syncFullscreen);
});
defineExpose({ seek, handleShortcut, setInspectionTime });
</script>
<template>
  <section
    ref="root"
    class="player"
    :class="{ 'player--timeline-expanded': timelineExpanded }"
    aria-label="影片播放器"
  >
    <div class="video-wrap" :style="{ aspectRatio: mediaAspectRatio }">
      <video
        :key="src"
        ref="video"
        :src="src"
        preload="metadata"
        playsinline
        aria-label="比賽影片"
        @click="toggle"
        @loadedmetadata="updateMetadata"
        @durationchange="updateMetadata"
        @timeupdate="sync(media($event))"
        @seeked="sync(media($event))"
        @play="sync(media($event))"
        @pause="sync(media($event))"
        @ended="sync(media($event))"
        @ratechange="sync(media($event))"
        @volumechange="syncMute"
        @error="failed(media($event))"
      ></video>
      <div
        v-if="state.paused && activeStroke"
        class="evidence-hud"
        aria-live="polite"
      >
        <strong
          >#{{ String(activeStroke.ordinal).padStart(2, "0") }} ·
          {{ formatPreciseTime(activeStroke.time) }}</strong
        >
        <span
          >{{ activeStroke.type ?? "球種未提供" }} ·
          {{ playerName(activeStroke.player) }}</span
        >
      </div>
    </div>
    <div class="controls">
      <div class="progress-control">
        <div ref="progressVisual" class="progress-visual" aria-hidden="true">
          <span class="progress-track-base" />
          <span
            v-if="activeRally"
            class="progress-rally-window"
            :style="{
              left: rallyStartPercent + '%',
              width: Math.max(0, rallyEndPercent - rallyStartPercent) + '%',
            }"
          />
          <span
            v-if="activeRally"
            class="progress-rally-boundary progress-rally-boundary--start"
            :style="{ left: rallyStartPercent + '%' }"
          />
          <span
            v-if="activeRally"
            class="progress-rally-boundary progress-rally-boundary--end"
            :style="{ left: rallyEndPercent + '%' }"
          />
          <span
            v-if="activeRally"
            class="progress-rally-locator"
            :style="{ left: rallyMidpointPercent + '%' }"
            aria-hidden="true"
          >
            <span
              class="rally-registration-index progress-rally-locator-label"
              :data-side="rallyLocatorSide"
              >{{ rallyIndex }}</span
            >
          </span>
          <span
            v-if="activeStroke"
            class="progress-stroke-tick"
            :style="{ left: activeStrokePercent + '%' }"
          />
          <span
            v-if="inspectionTime !== null"
            class="progress-inspection-tick"
            :style="{ left: inspectionPercent + '%' }"
          />
          <span
            class="progress-track-fill"
            :style="{ width: progressPercent + '%' }"
          />
          <span
            class="progress-thumb"
            :style="{ left: progressPercent + '%' }"
          />
        </div>
        <input
          aria-label="影片進度"
          :aria-valuetext="`${formatTime(state.time)} / ${formatTime(state.duration)}`"
          type="range"
          min="0"
          :max="state.duration || 1"
          step="any"
          :value="state.time"
          :disabled="!state.ready"
          @input="scrub"
        />
      </div>
      <div class="control-row">
        <button
          class="play-button"
          :aria-label="state.paused ? '播放' : '暫停'"
          @click="toggle"
        >
          <AppIcon :name="state.paused ? 'play' : 'pause'" :size="20" /></button
        ><span class="time-readout"
          >{{ formatTime(state.time) }}
          <span
            >/ {{ state.ready ? formatTime(state.duration) : "載入中" }}</span
          ></span
        ><span class="now-playing" aria-live="polite"
          ><strong>{{ context.label }}</strong
          ><small
            ><template v-if="context.score">{{ context.score }} · </template
            >{{ context.meta }}</small
          ></span
        ><button
          class="player-icon-button"
          type="button"
          :aria-label="muted ? '取消靜音' : '靜音'"
          @click="toggleMute"
        >
          <AppIcon :name="muted ? 'volume-muted' : 'volume'" /></button
        ><button
          class="player-icon-button"
          type="button"
          :aria-label="fullscreen ? '離開全螢幕' : '全螢幕'"
          @click="toggleFullscreen"
        >
          <AppIcon
            :name="fullscreen ? 'fullscreen-exit' : 'fullscreen'"
          /></button
        ><label
          >速度
          <select
            aria-label="播放速度"
            :value="state.rate"
            @change="changeRate"
          >
            <option v-for="rate in playbackRates" :key="rate" :value="rate">
              {{ rate }}×
            </option>
          </select></label
        >
      </div>
    </div>
    <p v-if="state.error" class="error" role="alert">{{ state.error }}</p>
  </section>
</template>
