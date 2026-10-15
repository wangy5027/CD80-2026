<script setup>
defineProps({
  scene: { type: String, default: 'entry' },
  paused: { type: Boolean, default: false },
});
</script>

<template>
  <div class="explore-ambient" data-testid="explore-ambient" :data-scene="scene" :data-paused="paused" aria-hidden="true">
    <div class="explore-ambient-field explore-ambient-base"></div>
    <div class="explore-ambient-field explore-ambient-light"></div>
    <div class="explore-ambient-field explore-ambient-color"></div>
    <div class="explore-ambient-wash"></div>
  </div>
</template>

<style scoped>
.explore-ambient {
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  contain: paint;
  pointer-events: none;
  background: #eeedf9;
}
.explore-ambient-field { position: absolute; inset: -10%; }
.explore-ambient-base {
  background:
    radial-gradient(ellipse at 48% 25%, #faf8ff 0, transparent 39%),
    radial-gradient(ellipse at 9% 55%, #b3dcfa 0, transparent 48%),
    radial-gradient(ellipse at 95% 67%, #7779ee 0, transparent 52%),
    linear-gradient(142deg, #99adf4, #e9e4ff 45%, #6d72d8);
  animation: explore-field-drift 28s ease-in-out infinite alternate;
}
.explore-ambient-light {
  background:
    radial-gradient(ellipse at 28% 52%, rgb(170 220 255 / 65%), transparent 44%),
    radial-gradient(ellipse at 61% 26%, rgb(252 249 255 / 78%), transparent 43%);
  animation: explore-light-pass 23s ease-in-out infinite alternate;
}
.explore-ambient-color {
  background:
    radial-gradient(ellipse at 83% 64%, rgb(101 97 234 / 38%), transparent 42%),
    radial-gradient(ellipse at 41% 17%, rgb(255 255 255 / 52%), transparent 35%);
  animation: explore-color-pass 31s ease-in-out infinite alternate;
}
.explore-ambient-wash {
  position: absolute;
  inset: 0;
  background: linear-gradient(116deg, rgb(237 245 255 / 82%), rgb(242 237 253 / 78%));
  opacity: 0;
  transition: opacity 800ms ease;
}
.explore-ambient[data-scene="research"] .explore-ambient-wash { opacity: .26; }
.explore-ambient[data-scene="report"] .explore-ambient-wash { opacity: .38; }
.explore-ambient[data-paused="true"] .explore-ambient-field { animation-play-state: paused; }
.explore-ambient[data-paused="true"] .explore-ambient-wash { transition: none; }
@keyframes explore-field-drift {
  from { transform: translate(-1%, 1%) scale(1); }
  to { transform: translate(3%, -2%) scale(1.06); }
}
@keyframes explore-light-pass {
  from { transform: translate(-5%, 4%) rotate(-2deg); }
  to { transform: translate(6%, -5%) rotate(3deg); }
}
@keyframes explore-color-pass {
  from { transform: translate(5%, -4%) scale(1.02); }
  to { transform: translate(-6%, 5%) scale(1.10); }
}
@media (max-width: 600px) {
  .explore-ambient-light { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .explore-ambient-field { animation-play-state: paused; }
  .explore-ambient-wash { transition: none; }
}
</style>
