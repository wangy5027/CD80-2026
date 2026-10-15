<script setup>
import { computed, nextTick, onMounted, ref } from 'vue';
import Icon from './Icon.vue';
import { publicSearchSources, publicSkillsUsed, publicToolsUsed } from '../search.js';

const props = defineProps({
  sources: { type: Array, default: () => [] },
  skills: { type: Array, default: () => [] },
  tools: { type: Array, default: () => [] },
  focusUrl: { type: String, default: '' },
});
const emit = defineEmits(['close']);
const panel = ref(null);
const closeButton = ref(null);
const list = ref(null);
const safeSkills = computed(() => publicSkillsUsed(props.skills));
const safeTools = computed(() => publicToolsUsed(props.tools));
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: 'long', day: 'numeric', timeZone: 'Asia/Shanghai',
});
const rows = computed(() => publicSearchSources(props.sources).map((source) => {
  const date = source.publishedAt ? new Date(source.publishedAt) : null;
  const hasDate = date && !Number.isNaN(date.getTime());
  return {
    ...source,
    domain: new URL(source.url).hostname.replace(/^www\./i, ''),
    dateLabel: hasDate ? dateFormatter.format(date) : '未标注日期',
    dateTime: hasDate ? date.toISOString() : '',
  };
}));

onMounted(async () => {
  await nextTick();
  const index = rows.value.findIndex((source) => source.url === props.focusUrl);
  if (index >= 0) {
    list.value?.querySelectorAll('.sources-panel-link')[index]?.focus();
  } else {
    closeButton.value?.focus();
  }
});

function onPanelKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    emit('close');
    return;
  }
  if (event.key !== 'Tab') return;
  const controls = [...(panel.value?.querySelectorAll('button:not([disabled]), a[href]') || [])];
  if (!controls.length) {
    event.preventDefault();
    panel.value?.focus();
    return;
  }
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && (document.activeElement === first || !panel.value.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !panel.value.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
}
</script>

<template>
  <div class="sources-panel-overlay" @click.self="emit('close')">
    <aside ref="panel" class="sources-panel" role="dialog" aria-modal="true" aria-labelledby="sources-panel-title" tabindex="-1" @keydown="onPanelKeydown">
      <div class="sources-panel-head">
        <div><h2 id="sources-panel-title">{{ rows.length ? '搜索来源' : '本次详情' }}</h2><p v-if="rows.length">{{ rows.length }} 条可查看的来源</p></div>
        <button ref="closeButton" type="button" class="sources-panel-close" aria-label="关闭来源" @click="emit('close')"><Icon name="close" small /></button>
      </div>
      <div class="sources-panel-scroll">
        <ol ref="list" class="sources-panel-list">
          <li v-for="(source, index) in rows" :key="source.url" class="sources-panel-item" :class="{ highlighted: source.url === focusUrl }">
            <a class="sources-panel-link" :href="source.url" target="_blank" rel="noopener noreferrer" :aria-label="`打开来源 ${index + 1}：${source.title}，${source.domain}`">
              <span class="sources-panel-number" aria-hidden="true">{{ index + 1 }}</span>
              <span class="sources-panel-copy"><span class="sources-panel-title">{{ source.title }}</span><span class="sources-panel-meta"><span>{{ source.domain }}</span><span aria-hidden="true">·</span><time v-if="source.dateTime" :datetime="source.dateTime">{{ source.dateLabel }}</time><span v-else>{{ source.dateLabel }}</span></span></span>
              <span class="sources-panel-external" aria-hidden="true">↗</span>
            </a>
          </li>
        </ol>
        <section v-if="safeSkills.length" class="sources-panel-skills" aria-label="本次使用的技能"><h3>本次使用的技能</h3><div><span v-for="skill in safeSkills" :key="skill" class="sources-panel-skill">{{ skill }}</span></div></section>
        <section v-if="safeTools.length" class="sources-panel-skills" aria-label="本次调用的工具"><h3>本次调用的工具</h3><div><span v-for="tool in safeTools" :key="tool" class="sources-panel-skill">{{ tool }}</span></div></section>
      </div>
    </aside>
  </div>
</template>
