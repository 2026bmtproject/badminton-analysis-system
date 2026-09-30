/** Media element is the only clock. Kept separate for deterministic state regressions. */
export type Media = Pick<
  HTMLVideoElement,
  | "currentTime"
  | "duration"
  | "paused"
  | "readyState"
  | "playbackRate"
  | "play"
  | "pause"
>;
export const initialPlayback = () => ({
  time: 0,
  duration: 0,
  paused: true,
  ready: false,
  error: "",
  rate: 1,
});
export function createPlayback(state = initialPlayback()) {
  let active: Media | null = null,
    pending: number | null = null,
    generation = 0;
  function reset() {
    const old = active;
    active = null;
    generation++;
    pending = null;
    old?.pause();
    Object.assign(state, initialPlayback());
  }
  function sync(el: Media | null = active) {
    if (!el || el !== active) return;
    if (state.ready) state.time = el.currentTime;
    state.paused = el.paused;
    state.rate = el.playbackRate;
  }
  function seek(value: number) {
    if (!Number.isFinite(value)) return;
    if (!active || !state.ready) {
      pending = Math.max(0, value);
      return;
    }
    active.currentTime = Math.min(state.duration, Math.max(0, value));
    sync();
  }
  function metadata(el: Media) {
    if (
      el !== active ||
      el.readyState < 1 ||
      !Number.isFinite(el.duration) ||
      el.duration <= 0
    )
      return;
    state.ready = true;
    state.duration = el.duration;
    state.error = "";
    if (pending !== null) {
      const target = pending;
      pending = null;
      seek(target);
    }
    sync();
  }
  function attach(el: Media | null) {
    active = el;
    if (el) {
      el.playbackRate = state.rate;
      metadata(el);
    }
  }
  async function toggle() {
    const el = active,
      token = generation;
    if (!el) return;
    try {
      if (el.paused) await el.play();
      else el.pause();
      if (token === generation && el === active) {
        state.error = "";
        sync();
      }
    } catch {
      if (token === generation && el === active)
        state.error = "無法開始播放，請重試或檢查影片來源。";
    }
  }
  function pause() {
    active?.pause();
    sync();
  }
  function setRate(value: number) {
    if (![0.5, 0.75, 1, 1.5, 2].includes(value)) return;
    state.rate = value;
    if (active) active.playbackRate = value;
  }
  function failed(el: Media) {
    if (el !== active) return;
    state.ready = false;
    state.error = "影片讀取失敗，請檢查影片來源。";
    sync();
  }
  return {
    state,
    reset,
    attach,
    sync,
    seek,
    metadata,
    toggle,
    pause,
    setRate,
    failed,
  };
}
