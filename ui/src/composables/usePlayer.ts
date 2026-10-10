import { reactive, watch, onBeforeUnmount, type Ref } from "vue";
import { createPlayback, initialPlayback } from "../playback";
export function usePlayer(
  video: Ref<HTMLVideoElement | null>,
  source: () => string,
) {
  const state = reactive(initialPlayback());
  const control = createPlayback(state);
  watch(source, () => control.reset(), { immediate: true, flush: "sync" });
  watch(video, (el) => control.attach(el), { flush: "sync" });
  onBeforeUnmount(() => {
    control.reset();
  });
  return control;
}
