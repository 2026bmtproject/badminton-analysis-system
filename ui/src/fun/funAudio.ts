/**
 * Party sounds on Web Audio: one looping song at a time plus overlapping one-shots. Files load on first
 * use; until a file lands the call is simply skipped, and the next frame asks again.
 */
export type LoopRequest = {
  url: string;
  loopStart: number;
  loopEnd: number;
  /** Where the song should be now; omitted, it carries on from where it stopped. */
  position?: number;
  rate: number;
};

/** A running song drifting further than this from where it should be is restarted in place. */
const RESYNC_SEC = 0.12;
const FADE_SEC = 0.06;

export class FunAudio {
  private context: AudioContext | null = null;
  private output: GainNode | null = null;
  private buffers = new Map<string, AudioBuffer | Promise<AudioBuffer | null> | null>();
  private loop: { url: string; source: AudioBufferSourceNode; fade: GainNode; startedAt: number; from: number; rate: number; request: LoopRequest } | null = null;
  /** Where each song stopped, so a song with no set position resumes there. */
  private resume = new Map<string, number>();
  private shots: { source: AudioBufferSourceNode; fade: GainNode }[] = [];

  private audio() {
    if (!this.context) {
      this.context = new AudioContext();
      this.output = this.context.createGain();
      this.output.connect(this.context.destination);
    }
    if (this.context.state === "suspended") void this.context.resume();
    return this.context;
  }

  private buffer(url: string): AudioBuffer | null {
    const held = this.buffers.get(url);
    if (held instanceof AudioBuffer) return held;
    if (held === undefined) {
      const context = this.audio();
      this.buffers.set(url, fetch(url).then((response) => response.arrayBuffer()).then((data) => context.decodeAudioData(data))
        .then((decoded) => { this.buffers.set(url, decoded); return decoded; })
        .catch(() => { this.buffers.set(url, null); return null; }));
    }
    return null;
  }

  /** Starts fetching files ahead of their first use. */
  preload(...urls: string[]) { for (const url of urls) this.buffer(url); }

  /** Follows the video's volume and mute. */
  volume(value: number) {
    if (this.output && this.context) this.output.gain.setTargetAtTime(value, this.context.currentTime, 0.02);
  }

  private position(loop: NonNullable<FunAudio["loop"]>) {
    const { loopStart, loopEnd } = loop.request;
    const raw = loop.from + (this.context!.currentTime - loop.startedAt) * loop.rate;
    return raw < loopEnd ? raw : loopStart + (raw - loopStart) % (loopEnd - loopStart);
  }

  /** Keeps `request` playing; call it every frame while the song should sound. */
  play(request: LoopRequest) {
    const buffer = this.buffer(request.url);
    if (!buffer) return;
    const loop = this.loop;
    if (loop && loop.url === request.url && loop.rate === request.rate &&
        (request.position === undefined || Math.abs(this.position(loop) - request.position) < RESYNC_SEC)) return;
    const from = request.position ?? (loop?.url === request.url ? this.position(loop) : this.resume.get(request.url) ?? request.loopStart);
    this.stop();
    const context = this.audio();
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    source.loopStart = request.loopStart;
    source.loopEnd = request.loopEnd;
    source.playbackRate.value = request.rate;
    const fade = context.createGain();
    fade.gain.setValueAtTime(0, context.currentTime);
    fade.gain.linearRampToValueAtTime(1, context.currentTime + FADE_SEC);
    source.connect(fade).connect(this.output!);
    source.start(context.currentTime, from);
    this.loop = { url: request.url, source, fade, startedAt: context.currentTime, from, rate: request.rate, request };
  }

  /** Fades the song out, remembering where it was. */
  stop() {
    const loop = this.loop;
    if (!loop || !this.context) return;
    this.resume.set(loop.url, this.position(loop));
    this.release(loop.source, loop.fade);
    this.loop = null;
  }

  /** Plays a sound once; sounds overlap, so a quick rally clangs on every hit. */
  shot(url: string) {
    const buffer = this.buffer(url);
    if (!buffer) return;
    const context = this.audio();
    const source = context.createBufferSource();
    source.buffer = buffer;
    const fade = context.createGain();
    source.connect(fade).connect(this.output!);
    source.start();
    const entry = { source, fade };
    this.shots.push(entry);
    source.onended = () => { this.shots = this.shots.filter((item) => item !== entry); };
  }

  /** Silences everything now playing. */
  hush() {
    this.stop();
    for (const { source, fade } of this.shots) this.release(source, fade);
    this.shots = [];
  }

  private release(source: AudioBufferSourceNode, fade: GainNode) {
    const now = this.context!.currentTime;
    fade.gain.cancelScheduledValues(now);
    fade.gain.setValueAtTime(fade.gain.value, now);
    fade.gain.linearRampToValueAtTime(0, now + FADE_SEC);
    source.stop(now + FADE_SEC);
  }

  dispose() {
    this.hush();
    void this.context?.close();
    this.context = null;
    this.output = null;
    this.buffers.clear();
  }
}
