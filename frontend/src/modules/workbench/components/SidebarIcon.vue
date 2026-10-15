<script setup>
import { computed } from 'vue';
import { fluentNavNames, resolveFluentNavGlyph } from '../assets/fluent-nav/index.js';

// Decorative only. The surrounding button/link owns its text, accessible name and route state.
const props = defineProps({
  name: { type: String, required: true, validator: value => fluentNavNames.includes(value) },
  active: { type: Boolean, default: false },
  size: { type: Number, default: 20, validator: value => Number.isFinite(value) && value >= 12 && value <= 32 },
});
const glyph = computed(() => resolveFluentNavGlyph(props.name, props.active));
</script>

<template>
  <svg
    class="fluent-nav-icon"
    :class="{ 'is-active': active }"
    :width="size"
    :height="size"
    :style="{ width: `${size}px`, height: `${size}px` }"
    :viewBox="glyph.viewBox"
    :data-fluent-nav="name"
    :data-variant="glyph.variant"
    fill="currentColor"
    stroke="none"
    aria-hidden="true"
    focusable="false"
    v-html="glyph.body"
  />
</template>

<style scoped>
.fluent-nav-icon {
  display: inline-block;
  flex: 0 0 auto;
  vertical-align: middle;
  fill: currentColor;
  stroke: none;
  stroke-width: 0;
  pointer-events: none;
}
</style>
