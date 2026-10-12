<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { OverlayManifestModel, RallyModel } from "../../domain/models";
import { createOverlayLoader } from "../../overlay/overlayChunk";
import type { OverlayLayerId } from "../../overlay/overlaySettings";
import { drawOverlay, readOverlayPalette, type OverlayPalette, type OverlayView } from "../../overlay/drawOverlay";
import { buildOverlayScene, containedRect, frameAtMediaTime, frameAtTime, rallyAtFrame } from "../../overlay/overlayScene";
import { rallyAtOrBefore } from "../../temporal/activeContext";
import { FunDirector } from "../../fun/funDirector";
import { replacesShuttle, type FunMode } from "../../fun/funModes";

const props = defineProps<{
  video: HTMLVideoElement | null;
  fps: number;
  rallies: readonly RallyModel[];
  manifest: OverlayManifestModel | null;
  /** The layers that draw; none drawing leaves the canvas empty and idle. */
  layers: Record<OverlayLayerId, boolean>;
  methods: readonly string[];
  players: { a: string; b: string };
  /** Party mode; the oiia and nyan cats take the shuttle's place. */
  fun?: FunMode;
  /** Overlay only: a dark backdrop covers the picture, which keeps playing so the marks stay on its frames. */
  hideVideo?: boolean;
}>();

/** The next Rally's file is fetched this long before it starts. */
const PREFETCH_SEC = 3;

const canvas = ref<HTMLCanvasElement | null>(null);
const tint = ref<HTMLElement | null>(null);
const loader = computed(() => props.manifest ? createOverlayLoader(props.manifest) : null);
const fun = computed(() => props.fun ?? "off");
const drawing = computed(() => Object.values(props.layers).some(Boolean) || fun.value !== "off");
/** The method a party shuttle follows: the one hits are detected on when it is shown, else the first shown. */
const funMethod = computed(() => {
  const base = props.manifest?.baseMethod;
  return base && props.methods.includes(base) ? base : props.methods[0] ?? null;
});
/** A cat draws one shuttle, so the plain shuttle and its trail step aside. */
const sceneLayers = computed(() => replacesShuttle(fun.value) ? { ...props.layers, shuttle: false, trail: false } : props.layers);
const director = new FunDirector();
let palette: OverlayPalette | null = null;
/** Media time of the frame on screen, from the frame callback; null until one arrives after a seek. */
let presented: number | null = null;
let frameCallback: { video: HTMLVideoElement; id: number } | null = null;
let queued = 0;
let observer: ResizeObserver | undefined;

function pictureView(element: HTMLCanvasElement, video: HTMLVideoElement): OverlayView | null {
  const host = element.getBoundingClientRect();
  const box = video.getBoundingClientRect();
  // Layout size, not the bounding box: the pipe's shake rotates the picture, which would swell the box.
  const rect = containedRect({ width: video.clientWidth, height: video.clientHeight }, { width: video.videoWidth, height: video.videoHeight });
  return rect ? { ...rect, x: box.left - host.left + rect.x, y: box.top - host.top + rect.y } : null;
}

function currentFrame(video: HTMLVideoElement) {
  return presented !== null ? frameAtMediaTime(presented, props.fps) : frameAtTime(video.currentTime, props.fps);
}

/** Loads the Rally on screen and the next one when it is close, then redraws once a file lands. */
function prefetch(frame: number, rallyId: number | null) {
  const files = loader.value;
  if (!files) return;
  const want = (id: number) => { if (files.peek(id) === undefined) void files.load(id).then(() => { if (files === loader.value) queueDraw(); }); };
  if (rallyId !== null) want(rallyId);
  const time = frame / props.fps;
  const current = rallyAtOrBefore(props.rallies, time);
  const next = props.rallies[current ? props.rallies.indexOf(current) + 1 : 0];
  if (next && next.start - time <= PREFETCH_SEC) want(next.id);
}

function draw() {
  const element = canvas.value;
  const video = props.video;
  if (!element) return;
  const width = element.clientWidth, height = element.clientHeight;
  const ratio = window.devicePixelRatio || 1;
  const pixelWidth = Math.round(width * ratio), pixelHeight = Math.round(height * ratio);
  if (element.width !== pixelWidth || element.height !== pixelHeight) {
    element.width = pixelWidth;
    element.height = pixelHeight;
  }
  const context = element.getContext("2d");
  if (!context) return;
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, element.width, element.height);
  if (!drawing.value || !video || video.readyState < 1) {
    if (video) director.frame({ mode: "off", video, frame: 0, fps: props.fps, rally: null, rallies: [], chunk: null, method: null,
      shuttle: false, trail: false, view: null, context, tint: tint.value });
    return;
  }
  const view = pictureView(element, video);
  if (!view) return;
  const frame = currentFrame(video);
  const rally = rallyAtFrame(props.rallies, frame, props.fps);
  prefetch(frame, rally?.id ?? null);
  const chunk = rally ? loader.value?.peek(rally.id) ?? null : null;
  const marks = buildOverlayScene({
    frame, rally, layers: sceneLayers.value, methods: props.methods, players: props.players, chunk, manifest: props.manifest,
  });
  palette ??= readOverlayPalette(element);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  drawOverlay(context, marks, view, palette, props.manifest?.methods);
  director.frame({
    mode: fun.value, video, frame, fps: props.fps, rally, rallies: props.rallies, chunk, method: funMethod.value,
    shuttle: props.layers.shuttle, trail: props.layers.trail, view, context, tint: tint.value,
  });
}

/** Coalesces redraws from settings, resizes and loaded files into one per animation frame. */
function queueDraw() {
  if (queued) return;
  queued = requestAnimationFrame(() => { queued = 0; draw(); });
}

function stopFrames() {
  if (frameCallback) frameCallback.video.cancelVideoFrameCallback(frameCallback.id);
  frameCallback = null;
}

/**
 * Draws on every presented video frame, so marks change exactly when the picture does; a seek while
 * paused presents a frame too. Waiting for the next frame costs nothing while paused.
 */
function followFrames() {
  const video = props.video;
  if (!video || !drawing.value || frameCallback) return;
  const id = video.requestVideoFrameCallback((_now, metadata) => {
    frameCallback = null;
    presented = metadata.mediaTime;
    draw();
    followFrames();
  });
  frameCallback = { video, id };
}

function seeking() { presented = null; director.seeked(); }
function refresh() { followFrames(); queueDraw(); }
function paused() { director.paused(); }
/** Each media event and its handler. */
const MEDIA_EVENTS: readonly [string, () => void][] = [
  ["seeking", seeking], ["seeked", refresh], ["play", refresh], ["loadedmetadata", refresh], ["resize", refresh],
  ["pause", paused], ["ended", paused], ["emptied", paused],
];

watch(() => props.video, (video, previous) => {
  stopFrames();
  presented = null;
  director.seeked();
  for (const [name, handler] of MEDIA_EVENTS) previous?.removeEventListener(name, handler);
  for (const [name, handler] of MEDIA_EVENTS) video?.addEventListener(name, handler);
  if (video) observer?.observe(video);
  refresh();
});
watch(drawing, (value) => { if (!value) stopFrames(); refresh(); });
// Each prop arrives as a fresh object when it changes, so a shallow watch sees every change without walking the Rallies.
watch(() => [props.layers, props.methods, props.manifest, props.rallies, props.players, props.fps, props.fun], queueDraw);

onMounted(() => {
  observer = new ResizeObserver(queueDraw);
  if (canvas.value) observer.observe(canvas.value);
  if (props.video) {
    observer.observe(props.video);
    for (const [name, handler] of MEDIA_EVENTS) props.video.addEventListener(name, handler);
  }
  refresh();
});
onBeforeUnmount(() => {
  stopFrames();
  if (queued) cancelAnimationFrame(queued);
  observer?.disconnect();
  for (const [name, handler] of MEDIA_EVENTS) props.video?.removeEventListener(name, handler);
  director.dispose();
});
</script>

<template>
  <div v-if="hideVideo" class="video-overlay-backdrop" aria-hidden="true" />
  <div ref="tint" class="video-overlay-tint" hidden aria-hidden="true" />
  <canvas ref="canvas" class="video-overlay" aria-hidden="true" />
</template>
