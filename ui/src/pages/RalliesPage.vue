<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import RallyBrowser from "../components/inspector/RallyBrowser.vue";
import { useMatchContext } from "../state/matchContext";

const { model } = useMatchContext();
const heading = ref<HTMLElement | null>(null);
const match = computed(() => {
  if (!model.value) throw new Error("Rallies requires a loaded MatchModel");
  return model.value;
});
watch(
  model,
  async () => {
    await nextTick();
    heading.value?.focus();
  },
  { immediate: true },
);
</script>

<template>
  <main class="rallies-page">
    <h1 ref="heading" class="sr-only" tabindex="-1">片段導覽</h1>
    <RallyBrowser :model="match" />
  </main>
</template>
