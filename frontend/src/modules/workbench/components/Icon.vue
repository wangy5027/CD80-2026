<script setup>
import { iconPaths } from '../icons.js';

defineProps({ name: { type: String, required: true }, small: Boolean });
</script>

<template>
  <svg class="icon" :class="{ 'icon-sm': small }" viewBox="0 0 24 24" aria-hidden="true" v-html="iconPaths[name] || iconPaths.info" />
</template>
