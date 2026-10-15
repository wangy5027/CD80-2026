<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { capGet, capabilityStatus, capabilitySelectionReason } from '../capabilityApi.js';
import { request } from '../api.js';
const props = defineProps({ kind: String, selected: { type: Array, default: () => [] } });
const emit = defineEmits(['choose', 'close', 'manage']);
const isSelected = item => Boolean(item.assetId && props.selected.some(ref => ref.assetId === item.assetId));
const reason = item => props.kind === 'application' ? '' : capabilitySelectionReason(item, isSelected(item));
const items = ref([]), query = ref(''), loading = ref(true), error = ref(''), search = ref(null);
const controller = new AbortController();
onUnmounted(() => controller.abort());
const filtered = computed(() => items.value.filter(item => `${item.name} ${item.description}`.toLowerCase().includes(query.value.toLowerCase())));
onMounted(async () => {
  search.value?.focus();
  try {
    if (props.kind === 'application') {
      const result = await request('/agents', { signal: controller.signal });
      items.value = result.agents.map(entry => ({ ...entry, name: entry.spec.name, description: entry.spec.description || entry.spec.goal, source: entry.personal ? '个人应用' : '内置应用', available: true }));
    } else {
      let cursor;
      do {
        const result = await capGet(`/assets?scope=personal&kind=${props.kind}&limit=100${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`, { signal: controller.signal });
        items.value.push(...result.items.map(item => ({ ...item, source: item.sourceIdentity,
          versionId: item.activeVersionId || item.latestVersionId,
          available: item.enabled && ['verified', 'ready'].includes(item.readiness) && item.lifecycle === 'active', status: capabilityStatus(item) })));
        cursor = result.nextCursor;
      } while (cursor && !controller.signal.aborted);
    }
  } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message; }
  finally { loading.value = false; }
});
</script>
<template>
  <section class="extension-picker" aria-label="选择技能池" @keydown.esc.stop="emit('close')">
    <header><strong>选择 {{ kind === 'application' ? 'Application' : kind === 'skill' ? 'Skill' : 'Tool' }}</strong><button type="button" class="secondary-button" @click="emit('close')">关闭</button></header>
    <p>{{ kind === 'application' ? '本轮运行一个已有应用，发送时填写必要参数。' : '优先考虑本轮选中的能力；不可用项目请先到技能池核对验证要求、配置与启用状态。' }}</p>
    <input ref="search" v-model="query" type="search" aria-label="搜索技能池" placeholder="搜索名称或用途" />
    <p v-if="loading" role="status">正在读取目录…</p><p v-if="error" role="alert">{{ USER_FACING_ERROR }}</p>
    <div class="extension-options"><article v-for="item in filtered" :key="item.assetId || `${item.personal}:${item.agentRef}`">
      <button class="extension-option" type="button" :disabled="!item.available || isSelected(item)" @click="emit('choose', item)"><strong>{{ item.name }}</strong><span>{{ item.description }}</span><small>{{ item.source }} · {{ isSelected(item) ? '本轮已选择' : item.status || '可运行' }}</small></button>
      <div v-if="reason(item)" class="extension-reason"><p>{{ USER_FACING_ERROR }}</p><button v-if="!isSelected(item)" class="secondary-button" type="button" :aria-label="'去技能池处理 ' + item.name" @click="emit('manage', item)">去技能池处理 →</button></div>
    </article></div>
    <p v-if="!loading && !filtered.length">没有匹配项。可先到技能池保存个人副本，按版本准入要求完成配置、验证或启用。</p>
  </section>
</template>
<style scoped>
.extension-options article{border:1px solid var(--line);border-radius:10px;overflow:hidden}.extension-options .extension-option{width:100%;border:0;border-radius:0}.extension-reason{padding:0 14px 14px;background:var(--surface-2,#f5f7f3)}.extension-reason p{margin:0 0 10px;color:var(--text);font-size:12px}.extension-reason .secondary-button{display:inline-block;padding:7px 12px;background:var(--surface,#fff)}
.extension-picker{margin:12px 16px;padding:18px;background:var(--surface,#fff);border:1px solid var(--line,#dce3dc);border-radius:16px;text-align:left}.extension-picker header{display:flex;align-items:center;justify-content:space-between;gap:12px}.extension-picker p{font-size:13px;color:var(--muted);line-height:1.6}.extension-picker input{box-sizing:border-box;width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--text)}.extension-options{max-height:260px;overflow:auto;display:grid;gap:8px;margin-top:12px}.extension-options button{display:grid;gap:5px;text-align:left;padding:14px;border:1px solid var(--line);border-radius:10px;background:var(--surface-2,#f5f7f3);color:var(--text);cursor:pointer}.extension-options span{font-size:13px;line-height:1.5}.extension-options small{color:var(--muted);overflow-wrap:anywhere}.extension-options button:disabled{opacity:.55;cursor:not-allowed}.extension-options button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style>
