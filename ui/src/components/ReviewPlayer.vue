<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { usePlayer } from "../composables/usePlayer";
import { formatTime } from "../format";
import type { PlayerShortcutAction } from "../interaction/playerShortcuts";
import { segmentSkipTarget } from "../temporal/segmentPlayback";
import AppIcon from "./ui/AppIcon.vue";
const props = withDefaults(defineProps<{
  src: string;
  fullscreen?: boolean;
  /** Element that hosts the controls instead of the player, e.g. the floating fullscreen timeline. */
  controlsTarget?: HTMLElement | null;
  /** Sorted Segment intervals; segments-only playback skips the gaps between them. */
  segments?: readonly { start: number; end: number }[];
  segmentsOnly?: boolean;
}>(), { fullscreen: false, controlsTarget: null, segments: () => [], segmentsOnly: false });
const emit = defineEmits<{ time: [value: number]; playing: [value: boolean]; fullscreenToggle: []; "update:segmentsOnly": [value: boolean] }>();
const video = ref<HTMLVideoElement | null>(null);
const mediaAspectRatio = ref("16 / 9");
const muted = ref(false);
const playbackRates = [0.5, 0.75, 1, 1.5, 2] as const;
const { state, seek, metadata, sync, toggle, setRate, failed } = usePlayer(
  video,
  () => props.src,
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
  else if (action === "toggle-fullscreen") emit("fullscreenToggle");
  else if (action === "rate-down") stepRate(-1);
  else if (action === "rate-up") stepRate(1);
  else if (action === "toggle-segments-only") toggleSegmentsOnly();
}
function toggleSegmentsOnly() {
  if (props.segments.length) emit("update:segmentsOnly", !props.segmentsOnly);
}
let skipFrame = 0;
/** The first check after playback starts: play pressed past the last Segment restarts from the first. */
let skipFresh = false;
function stopSkipping() {
  if (skipFrame) cancelAnimationFrame(skipFrame);
  skipFrame = 0;
}
/** Checks every frame, since `timeupdate` would overrun a Segment end by up to a quarter second. */
function skipGaps() {
  skipFrame = 0;
  const element = video.value;
  if (!element || element.paused) return;
  const target = segmentSkipTarget(props.segments, element.currentTime);
  const fresh = skipFresh;
  skipFresh = false;
  if (target === "end") {
    if (!fresh) { element.pause(); return; }
    seek(props.segments[0]!.start);
  } else if (target !== null) seek(target);
  skipFrame = requestAnimationFrame(skipGaps);
}
watch(
  () => props.segmentsOnly && props.segments.length > 0 && state.ready && !state.paused,
  (active) => {
    stopSkipping();
    if (!active) return;
    skipFresh = true;
    skipGaps();
  },
  { flush: "post" },
);
onBeforeUnmount(stopSkipping);
watch(
  () => props.src,
  () => {
    mediaAspectRatio.value = "16 / 9";
    muted.value = false;
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
function pause() { video.value?.pause(); }
/** Frame-accurate media time; `timeupdate` only refreshes `state.time` about four times a second. */
function currentTime() { return video.value?.currentTime ?? state.time; }
defineExpose({ seek, pause, handleShortcut, currentTime });
</script>
<template>
  <section
    class="player"
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
    </div>
    <Teleport :to="controlsTarget ?? 'body'" :disabled="!controlsTarget">
      <div class="controls">
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
          ><button
            class="player-icon-button player-icon-button--segments"
            type="button"
            aria-label="只播片段"
            :title="segmentsOnly ? '只播片段：開（S）' : '只播片段：關（S）'"
            :aria-pressed="segmentsOnly"
            :disabled="!segments.length"
            @click="toggleSegmentsOnly"
          >
            <AppIcon name="segments" /></button
          ><button
            class="player-icon-button"
            type="button"
            :aria-label="muted ? '取消靜音' : '靜音'"
            @click="toggleMute"
          >
            <AppIcon :name="muted ? 'volume-muted' : 'volume'" /></button
          ><button
            class="player-icon-button player-icon-button--fullscreen"
            type="button"
            :aria-label="fullscreen ? '離開全螢幕' : '全螢幕'"
            @click="emit('fullscreenToggle')"
          >
            <AppIcon
              :name="fullscreen ? 'fullscreen-exit' : 'fullscreen'"
            /></button
          ><label
            ><span class="control-label">速度</span>
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
    </Teleport>
    <p v-if="state.error" class="error" role="alert">{{ state.error }}</p>
  </section>
</template>
