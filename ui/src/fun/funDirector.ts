import type { RallyModel } from "../domain/models";
import { shuttleAt, type OverlayChunk } from "../overlay/overlayChunk";
import { shuttleTrail } from "../overlay/overlayScene";
import type { OverlayView } from "../overlay/drawOverlay";
import nyanPng from "../assets/fun/nyan.png";
import nyanSong from "../assets/fun/nyan.mp3";
import oiiaSong from "../assets/fun/oiia.mp3";
import oiiaSprite from "../assets/fun/oiia-sprite.webp";
import pipeSound from "../assets/fun/pipe.mp3";
import { FunAudio } from "./funAudio";
import {
  CatAppearance, crossedHits, NYAN_HEIGHT, NYAN_LOOP, NYAN_RAINBOW, NYAN_TRAIL, NYAN_WIGGLE, OIIA, OIIA_HEIGHT,
  oiiaFrame, oiiaSongPosition, partying, partyLight, PIPE_SHAKE, PIPE_SHAKE_MS, rainbowCells, type FunMode,
} from "./funModes";

export type FunFrame = {
  mode: FunMode;
  video: HTMLVideoElement;
  frame: number;
  fps: number;
  rally: RallyModel | null;
  rallies: readonly RallyModel[];
  chunk: OverlayChunk | null;
  /** The tracking method the party shuttle follows. */
  method: string | null;
  /** Whether the shuttle and its trail layers are drawing. */
  shuttle: boolean;
  trail: boolean;
  /** The picture inside the canvas; null while there is none. */
  view: OverlayView | null;
  context: CanvasRenderingContext2D;
  /** Coloured light over the picture, under the canvas, so the drawn marks keep their colours. */
  tint: HTMLElement | null;
};

/** The longest forward step that still counts as playing through a hit, in seconds. */
const MAX_PLAY_STEP = 0.5;
/** Picture width the sizes are tuned for. */
const BASE_WIDTH = 1280;

function image(url: string) {
  const element = new Image();
  element.decoding = "async";
  element.src = url;
  return element;
}

/** Runs the party modes on top of the overlay, frame by frame; one per video overlay. */
export class FunDirector {
  private audio = new FunAudio();
  private cat: HTMLImageElement | null = null;
  private nyan: HTMLImageElement | null = null;
  private appearance = new CatAppearance();
  private lastFrame: number | null = null;
  private shake: Animation | null = null;
  private lit: { video: HTMLVideoElement; tint: HTMLElement | null } | null = null;
  private mode: FunMode = "off";

  /** Called once per drawn frame, after the overlay marks. */
  frame(input: FunFrame) {
    const { mode, video, view } = input;
    if (mode !== this.mode) this.switchTo(mode);
    this.audio.volume(video.muted ? 0 : video.volume);
    const time = input.frame / input.fps;
    const point = input.shuttle && input.method ? shuttleAt(input.chunk, input.method, input.frame) : null;

    if (mode === "oiia") {
      const elapsed = this.appearance.update(time, point !== null);
      const position = elapsed === null ? OIIA.audio.loopStart : oiiaSongPosition(elapsed);
      if (elapsed !== null && !video.paused) {
        this.audio.play({ url: oiiaSong, ...OIIA.audio, position, rate: video.playbackRate });
      } else this.audio.stop();
      if (point && view) this.drawCat(input.context, view, point, position);
      this.light(video, input.tint, view, partying(input.rally, time) ? time : null);
    } else if (mode === "nyan") {
      if (!video.paused) this.audio.play({ url: nyanSong, ...NYAN_LOOP, rate: 1 });
      else this.audio.stop();
      if (view && input.method) this.drawNyan(input, view, point);
    } else if (mode === "pipe") {
      const from = this.lastFrame;
      if (from !== null && !video.paused && crossedHits(input.rallies, from, input.frame, MAX_PLAY_STEP * input.fps).length) {
        this.audio.shot(pipeSound);
        this.shake?.cancel();
        this.shake = video.parentElement?.animate(PIPE_SHAKE, { duration: PIPE_SHAKE_MS, easing: "ease-out" }) ?? null;
      }
    }
    this.lastFrame = input.frame;
  }

  /** A seek is a new start: the cat's song starts over and no hit fires on the jump. */
  seeked() {
    this.appearance.reset();
    this.lastFrame = null;
  }

  /** The video paused or stopped; songs wait for it, a clang already ringing finishes. */
  paused() { this.audio.stop(); }

  dispose() {
    this.switchTo("off");
    this.audio.dispose();
  }

  private switchTo(mode: FunMode) {
    this.mode = mode;
    this.audio.hush();
    this.appearance.reset();
    this.lastFrame = null;
    this.shake?.cancel();
    this.shake = null;
    this.light(null, null, null, null);
    if (mode === "oiia") { this.cat ??= image(oiiaSprite); this.audio.preload(oiiaSong); }
    if (mode === "nyan") { this.nyan ??= image(nyanPng); this.audio.preload(nyanSong); }
    if (mode === "pipe") this.audio.preload(pipeSound);
  }

  /** Beat lights over the picture, or none when `time` is null. */
  private light(video: HTMLVideoElement | null, tint: HTMLElement | null, view: OverlayView | null, time: number | null) {
    if (time === null || !video || !view) {
      if (this.lit) {
        this.lit.video.style.filter = "";
        if (this.lit.tint) this.lit.tint.hidden = true;
        this.lit = null;
      }
      return;
    }
    const { multiply, brightness } = partyLight(time);
    video.style.filter = `brightness(${brightness.toFixed(3)})`;
    if (tint) {
      tint.hidden = false;
      Object.assign(tint.style, {
        left: `${view.x}px`, top: `${view.y}px`, width: `${view.width}px`, height: `${view.height}px`,
        backgroundColor: `rgb(${multiply.map((value) => Math.round(value * 255)).join(" ")})`,
      });
    }
    this.lit = { video, tint };
  }

  private drawCat(context: CanvasRenderingContext2D, view: OverlayView, point: readonly [number, number], position: number) {
    const sheet = this.cat;
    if (!sheet?.complete || !sheet.naturalWidth) return;
    const index = oiiaFrame(position);
    const { frameWidth, frameHeight, columns } = OIIA;
    const height = OIIA_HEIGHT * view.width / BASE_WIDTH;
    const width = height * frameWidth / frameHeight;
    const x = view.x + point[0] * view.scale, y = view.y + point[1] * view.scale;
    this.clipped(context, view, () => context.drawImage(sheet, (index % columns) * frameWidth, Math.floor(index / columns) * frameHeight,
      frameWidth, frameHeight, x - width / 2, y - height / 2, width, height));
  }

  private drawNyan(input: FunFrame, view: OverlayView, point: readonly [number, number] | null) {
    const sprite = this.nyan;
    const { context } = input;
    const unit = Math.max(1, Math.round(NYAN_HEIGHT * view.width / BASE_WIDTH / (sprite?.naturalHeight || 20)));
    const screen = (p: readonly [number, number]) => [view.x + p[0] * view.scale, view.y + p[1] * view.scale] as const;
    const runs = input.rally ? shuttleTrail(input.chunk, input.method!, input.frame, NYAN_TRAIL).map((run) => run.map(screen)) : [];
    const tick = Math.floor(input.frame / input.fps * NYAN_WIGGLE);
    this.clipped(context, view, () => {
      if (input.trail) {
        for (const run of runs) {
          rainbowCells(run, unit, tick).forEach((cells, band) => {
            context.fillStyle = NYAN_RAINBOW[band]!;
            context.beginPath();
            for (const [cx, cy] of cells) context.rect(cx * unit, cy * unit, unit, unit);
            context.fill();
          });
        }
      }
      if (!point || !sprite?.complete || !sprite.naturalWidth) return;
      const [x, y] = screen(point);
      const last = runs[runs.length - 1];
      const back = last?.[Math.max(last.length - 4, 0)] ?? [x, y];
      const width = sprite.naturalWidth * unit, height = sprite.naturalHeight * unit;
      context.imageSmoothingEnabled = false;
      context.translate(Math.round(x), Math.round(y));
      if (x < back[0]) context.scale(-1, 1);                       // flying left turns the cat round
      context.drawImage(sprite, -Math.round(width / 2), -Math.round(height / 2), width, height);
    });
  }

  private clipped(context: CanvasRenderingContext2D, view: OverlayView, paint: () => void) {
    context.save();
    context.beginPath();
    context.rect(view.x, view.y, view.width, view.height);
    context.clip();
    paint();
    context.restore();
  }
}
