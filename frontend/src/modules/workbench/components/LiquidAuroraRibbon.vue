<script setup>
import { onMounted, onUnmounted, ref, shallowRef, useId } from 'vue';

const ribbon = ref(null);
const id = `home-aurora-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
const paint = name => `url(#${id}-${name})`;
const palette = [[199, 165, 245], [230, 200, 250], [245, 187, 221], [255, 249, 255], [245, 187, 221], [216, 137, 230]];

function bezier(a, b, c, d, t) {
  const v = 1 - t;
  return v ** 3 * a + 3 * v * v * t * b + 3 * v * t * t * c + t ** 3 * d;
}

// Sample the original two curves once; each frame deforms the entire spine.
const base = Array.from({ length: 73 }, (_, i) => {
  const u = i / 72, first = u <= .5, t = first ? u * 2 : (u - .5) * 2;
  return {
    u,
    x: first ? bezier(80, 280, 345, 560, t) : bezier(560, 775, 860, 1020, t),
    y: 78 + ((first ? bezier(96, -40, 160, 82, t) : bezier(82, 4, -12, 96, t)) - 78) * .62,
    envelope: Math.sin(Math.PI * u) ** .65,
  };
});
const n = value => value.toFixed(2);

// Catmull-Rom tangents produce a continuous cubic path, including at the join.
function smooth(points, move = true) {
  let d = move ? `M${n(points[0].x)} ${n(points[0].y)}` : '';
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[Math.max(0, i - 1)], b = points[i];
    const c = points[i + 1], e = points[Math.min(points.length - 1, i + 2)];
    d += `C${n(b.x + (c.x - a.x) / 6)} ${n(b.y + (c.y - a.y) / 6)} ${n(c.x - (e.x - b.x) / 6)} ${n(c.y - (e.y - b.y) / 6)} ${n(c.x)} ${n(c.y)}`;
  }
  return d;
}

function liquidColor(phase) {
  const wrapped = ((phase % palette.length) + palette.length) % palette.length;
  const index = Math.floor(wrapped), fraction = wrapped - index;
  const mix = fraction * fraction * (3 - 2 * fraction);
  return `rgb(${palette[index].map((v, channel) => Math.round(v + (palette[(index + 1) % palette.length][channel] - v) * mix)).join(' ')})`;
}

function shape(time) {
  const breath = .5 + .5 * Math.sin(time * .43);
  const points = base.map(({ x, y, u, envelope }) => {
    const phase = .45 * Math.sin(time * .19 + u * 3.8);
    const swell = 28 * Math.sin(u * 7.4 - time * .68 + phase);
    const counterflow = 10 * Math.sin(u * 16.8 + time * .94);
    const drift = 9 * Math.sin(u * 2.8 + time * .29);
    const ripple = 3.2 * Math.sin(u * 31.5 - time * 1.12 + phase);
    const rawY = y + envelope * (swell + counterflow + drift + ripple);
    return {
      x: x + envelope * 4 * Math.sin(u * 6.5 + time * .37),
      // Ease the larger waves away from the headline and card edges without clipping.
      y: 28 + 6 * Math.log1p(Math.exp((rawY - 28) / 6)) - 6 * Math.log1p(Math.exp((rawY - 140) / 6)),
    };
  });
  const upper = [], lower = [], sheenUpper = [], sheenLower = [];
  points.forEach((point, i) => {
    const before = points[Math.max(0, i - 1)], after = points[Math.min(points.length - 1, i + 1)];
    const dx = after.x - before.x, dy = after.y - before.y, length = Math.hypot(dx, dy);
    const { u, envelope } = base[i];
    // A gently twisting, breathing cross-section shares the very same spine.
    const width = (.3 + .7 * envelope) * (7.6 + breath * 2.4 + 2.6 * Math.sin(u * 9.6 - time * .53) + 1.2 * Math.sin(u * 21 + time * .37));
    const nx = -dy / length * width, ny = dx / length * width;
    upper.push({ x: point.x + nx, y: point.y + ny });
    lower.push({ x: point.x - nx, y: point.y - ny });
    // A broad, softly twisting reflection gives the thick silk a curved surface.
    const fold = .16 + .14 * Math.sin(u * 7.8 + time * .36);
    const sheen = .32 + .07 * Math.sin(u * 13 - time * .28);
    sheenUpper.push({ x: point.x + nx * (fold + sheen), y: point.y + ny * (fold + sheen) });
    sheenLower.push({ x: point.x + nx * (fold - sheen), y: point.y + ny * (fold - sheen) });
  });
  lower.reverse();
  sheenLower.reverse();
  return {
    spine: smooth(points),
    body: `${smooth(upper)}L${n(lower[0].x)} ${n(lower[0].y)}${smooth(lower, false)}Z`,
    sheen: `${smooth(sheenUpper)}L${n(sheenLower[0].x)} ${n(sheenLower[0].y)}${smooth(sheenLower, false)}Z`,
    glowWidth: 40 + breath * 8,
    glowOpacity: .34 + breath * .10,
    reflectionWidth: 3.2 + breath * 2,
    x1: -65 + 85 * Math.sin(time * .17),
    x2: 1165 + 70 * Math.sin(time * .13 + 1),
    y1: 20 + 30 * Math.sin(time * .21),
    y2: 120 + 25 * Math.sin(time * .16),
    stops: Array.from({ length: 9 }, (_, i) => ({
      offset: i / 8 + .018 * Math.sin(time * .23 + i * .8) * Math.sin(Math.PI * i / 8),
      color: liquidColor(i * .85 - time * .30 + .38 * Math.sin(time * .11 + i * .7)),
    })),
    reflection: Array.from({ length: 5 }, (_, i) => ({
      offset: i / 4,
      color: liquidColor(2.45 + .85 * Math.sin(i * 1.7 + time * .31)),
      opacity: .60 + .35 * (.5 + .5 * Math.sin(i * 1.9 - time * .47)),
    })),
  };
}

const frame = shallowRef(shape(0));
let request = 0, previous = null, elapsed = 0;
let motionPreference, desktop, observer;
let visible = true;

function stop() {
  cancelAnimationFrame(request);
  request = 0;
  previous = null;
}

function animate(timestamp) {
  if (previous !== null) elapsed += Math.min((timestamp - previous) / 1000, .05);
  previous = timestamp;
  frame.value = shape(elapsed);
  request = requestAnimationFrame(animate);
}

function syncAnimation() {
  const reduced = motionPreference.matches;
  if (reduced || !desktop.matches || document.hidden || !visible) {
    stop();
    if (reduced) frame.value = shape(0);
  } else if (!request) {
    request = requestAnimationFrame(animate);
  }
}

onMounted(() => {
  motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  desktop = window.matchMedia('(min-width: 1024px)');
  motionPreference.addEventListener('change', syncAnimation);
  desktop.addEventListener('change', syncAnimation);
  document.addEventListener('visibilitychange', syncAnimation);
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; syncAnimation(); });
    observer.observe(ribbon.value);
  }
  syncAnimation();
});

onUnmounted(() => {
  stop();
  observer?.disconnect();
  motionPreference?.removeEventListener('change', syncAnimation);
  desktop?.removeEventListener('change', syncAnimation);
  document.removeEventListener('visibilitychange', syncAnimation);
});
</script>

<template>
  <svg ref="ribbon" class="liquid-aurora-ribbon" viewBox="0 0 1100 160" fill="none" preserveAspectRatio="none" aria-hidden="true" focusable="false">
    <defs>
      <path :id="`${id}-spine`" :d="frame.spine" />
      <linearGradient :id="`${id}-liquid`" gradientUnits="userSpaceOnUse" :x1="frame.x1" :y1="frame.y1" :x2="frame.x2" :y2="frame.y2">
        <stop v-for="(stop, i) in frame.stops" :key="i" :offset="stop.offset" :stop-color="stop.color" />
      </linearGradient>
      <linearGradient :id="`${id}-reflection`" gradientUnits="userSpaceOnUse" :x1="frame.x1" y1="0" :x2="frame.x2" y2="160">
        <stop v-for="(stop, i) in frame.reflection" :key="i" :offset="stop.offset" :stop-color="stop.color" :stop-opacity="stop.opacity" />
      </linearGradient>
      <filter :id="`${id}-bloom`" x="-10%" y="-100%" width="120%" height="300%" color-interpolation-filters="sRGB">
        <feGaussianBlur stdDeviation="5" />
      </filter>
      <filter :id="`${id}-soft`" x="-5%" y="-50%" width="110%" height="200%" color-interpolation-filters="sRGB">
        <feGaussianBlur stdDeviation="1.5" />
      </filter>
    </defs>
    <g stroke-linecap="round" stroke-linejoin="round">
      <use :href="`#${id}-spine`" :stroke="paint('liquid')" :stroke-width="frame.glowWidth" :opacity="frame.glowOpacity" :filter="paint('bloom')" />
      <use :href="`#${id}-spine`" :stroke="paint('liquid')" stroke-width="24" opacity=".52" :filter="paint('soft')" />
      <path :d="frame.body" fill="#C7A5F5" opacity=".32" />
      <path :d="frame.body" :fill="paint('liquid')" opacity=".94" />
      <path :d="frame.body" :stroke="paint('liquid')" stroke-width="1" opacity=".65" />
      <path :d="frame.sheen" :fill="paint('reflection')" opacity=".65" />
      <use :href="`#${id}-spine`" :stroke="paint('reflection')" :stroke-width="frame.reflectionWidth" opacity=".86" />
      <use :href="`#${id}-spine`" stroke="#FFF9FF" stroke-width="1" opacity=".64" />
    </g>
  </svg>
</template>

<style scoped>
.liquid-aurora-ribbon { overflow: visible; contain: layout style; }
</style>
