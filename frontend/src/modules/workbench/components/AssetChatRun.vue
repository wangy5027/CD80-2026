<script setup>
import { onMounted, onUnmounted, ref } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import AssetAppRun from './AssetAppRun.vue';
import { createAssetAppsApi } from '../assetAppsApi.js';
const props = defineProps({ ownerId: { type: String, required: true }, runId: { type: String, required: true }, appId: String, versionId: String, files: { type: Array, default: () => [] } });
const controller = new AbortController(), api = createAssetAppsApi(props.ownerId, controller.signal);
const run = ref(null), definition = ref(null), error = ref(''), busy = ref(false);
let timer, sequence = 0, definitionId = '';
async function load() {
  const at = ++sequence;
  try {
    const value = await api.runDetail(props.runId);
    if (at !== sequence || controller.signal.aborted) return;
    run.value = value;
    const appId = value.appId || props.appId, versionId = value.versionId || props.versionId;
    if (appId && definitionId !== appId + ':' + versionId) {
      const detail = await api.detail(appId, versionId);
      if (at !== sequence || controller.signal.aborted) return;
      definition.value = detail.definition; definitionId = appId + ':' + versionId;
    }
    error.value = '';
  } catch (cause) { if (at === sequence && cause.name !== 'AbortError') error.value = cause.message; }
  finally { if (!controller.signal.aborted && at === sequence) { clearTimeout(timer); if (!run.value || ['queued','running','validating','cancelling'].includes(run.value.status)) timer = setTimeout(load, 1400); } }
}
async function action(operation) {
  if (busy.value) return;
  busy.value = true; error.value = '';
  try { await operation(); await load(); }
  catch (cause) { if (!controller.signal.aborted) error.value = cause.message; }
  finally { busy.value = false; }
}
onMounted(load);
onUnmounted(() => { sequence++; clearTimeout(timer); controller.abort(); });
</script>
<template>
  <section class="asset-chat-run" aria-label="应用实际运行">
    <p v-if="error" role="alert">{{ USER_FACING_ERROR }} <button type="button" class="secondary-button" :disabled="busy" @click="load">刷新运行状态</button></p>
    <p v-if="!run" role="status">正在读取应用实际运行记录…</p>
    <AssetAppRun v-if="run && definition" :run="run" :definition="definition" :files="files" :busy="busy" @resume="action(() => api.resume(run.id,$event))" @cancel="action(() => api.cancel(run.id,run.revision))" />
  </section>
</template>
<style scoped>.asset-chat-run{margin:16px 0;padding:16px;border:1px solid var(--line,#d8e3e9);border-radius:12px}.asset-chat-run>p{font-size:12px;line-height:1.8;color:var(--muted,#657b89)}</style>
