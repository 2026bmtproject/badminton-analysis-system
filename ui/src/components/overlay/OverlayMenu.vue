<script setup lang="ts">
import { computed } from "vue";
import { BOTH_METHODS, OVERLAY_LAYERS, type OverlayAvailability, type OverlayLayerId, type OverlaySettings } from "../../overlay/overlaySettings";
import WorkspacePopover from "../workspace/WorkspacePopover.vue";
import AppIcon from "../ui/AppIcon.vue";

const props = defineProps<{
  settings: OverlaySettings;
  availability: OverlayAvailability;
  /** Shuttle tracking methods in this match. */
  methods: readonly string[];
}>();
const emit = defineEmits<{ toggle: []; layer: [id: OverlayLayerId]; method: [value: string]; open: [value: boolean] }>();

const methodOptions = computed(() => props.methods.length > 1 ? [...props.methods, BOTH_METHODS] : []);
const selectedMethod = computed(() => props.methods.includes(props.settings.shuttleMethod) ? props.settings.shuttleMethod : BOTH_METHODS);
/** Each reason once: a missing stage usually disables a few layers together. */
const reasons = computed(() => [...new Set(Object.values(props.availability).filter((reason): reason is string => reason !== null))]);
</script>

<template>
  <WorkspacePopover label="疊圖（O）" :min-width="200" :trigger-class="['player-icon-button', 'overlay-menu__trigger', { 'player-icon-button--on': settings.enabled }]" @open="emit('open', $event)">
    <template #trigger><AppIcon name="layers" /></template>
    <div class="overlay-menu">
      <label class="overlay-menu__row overlay-menu__row--master">
        <input type="checkbox" :checked="settings.enabled" @change="emit('toggle')" />
        <span>疊圖</span><kbd>O</kbd>
      </label>
      <div class="overlay-menu__layers" :class="{ 'overlay-menu__layers--off': !settings.enabled }">
        <label v-for="layer in OVERLAY_LAYERS" :key="layer.id" class="overlay-menu__row" :class="{ 'overlay-menu__row--child': layer.parent }" :title="availability[layer.id] ?? undefined">
          <input type="checkbox" :checked="settings.layers[layer.id]" :disabled="availability[layer.id] !== null" @change="emit('layer', layer.id)" />
          <span>{{ layer.label }}</span><kbd>{{ layer.key }}</kbd>
        </label>
      </div>
      <fieldset v-if="methodOptions.length" class="overlay-menu__methods" :disabled="availability.shuttle !== null">
        <legend>羽球軌跡</legend>
        <label v-for="option in methodOptions" :key="option">
          <input type="radio" name="overlay-shuttle-method" :value="option" :checked="selectedMethod === option" @change="emit('method', option)" />
          <span>{{ option === BOTH_METHODS ? "兩者" : option }}</span>
        </label>
      </fieldset>
      <p v-for="reason in reasons" :key="reason" class="overlay-menu__note">{{ reason }}</p>
    </div>
  </WorkspacePopover>
</template>
