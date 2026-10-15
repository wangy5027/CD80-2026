<script setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import AuthoringCard from './AuthoringCard.vue';
import { request } from '../api.js';
import { kindLabel } from '../authoringModel.js';
const props = defineProps({ controller: { type: Object, required: true }, ownerId: String, kinds: { type: Array, default: () => ['app','tool','skill'] }, refreshToken: Number });
const emit = defineEmits(['author']);
const originals = ref([]), error = ref('');
const sessions = computed(() => props.controller.s.items.filter(item => props.kinds.includes(item.kind)));
const abortController = new AbortController(); let sequence = 0;
async function load() {
  const at = ++sequence, owner = props.ownerId;
  try { const result = await request('/portable-assets', { signal: abortController.signal }); if (at === sequence && owner === props.ownerId) { originals.value = (result.items || []).filter(item => props.kinds.includes(item.kind)); error.value = ''; } }
  catch (cause) { if (at === sequence && cause.name !== 'AbortError') error.value = cause.message; }
}
watch(() => [props.ownerId, props.refreshToken], () => { originals.value = []; load(); }, { immediate: true });
onUnmounted(() => { sequence++; abortController.abort(); });
function prepare(item) { emit('author', { kind: item.kind, intent: 'prepare_import', instruction: '准备使用「' + item.name + '」，保留原有业务规则。', sourceRefs: [{ kind: 'import_draft', reference: { portableAssetId: item.id } }] }); }
</script>
<template>
  <section v-if="sessions.length || originals.length || error" class="author-shelf" aria-label="草稿与导入原件">
    <details :open="sessions.some(item => !['completed','cancelled'].includes(item.phase))"><summary>创作草稿与原件 <span>{{ sessions.length + originals.length }}</span></summary><p>继续同一份草稿、查看验证结果，或导出保存的原件。</p>
      <p v-if="error" role="alert">{{ USER_FACING_ERROR }} <button type="button" class="secondary-button" @click="load">重试读取原件</button></p>
      <div class="author-shelf-grid"><AuthoringCard v-for="session in sessions" :key="session.id" :session="session" :detail="controller.s.details[session.id]" :busy="controller.s.busy" @open="controller.read(session.id)" /></div>
      <article v-for="item in originals" :key="item.id" class="author-stored"><div><strong>{{ item.name || item.originalName }}</strong><small>{{ kindLabel(item.kind) }} · 原件已保存 · 待准备</small><small>{{ item.files?.length || 1 }} 个文件 · 保存原件不代表可运行</small></div><div><a class="secondary-button" :href="item.exportUrl" download>导出原件</a><button type="button" class="secondary-button" @click="prepare(item)">准备使用</button></div></article>
    </details>
  </section>
</template>
<style scoped>
.author-shelf{border:1px solid var(--line,#d8e3e9);border-radius:12px;padding:18px 20px;margin:0 0 24px}.author-shelf summary{font-size:14px;color:var(--text,#17374a);cursor:pointer}.author-shelf summary span{color:var(--muted,#657b89);margin-left:10px}.author-shelf p{font-size:12px;line-height:1.8;color:var(--muted,#657b89)}.author-shelf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));gap:12px}.author-stored{display:flex;align-items:center;justify-content:space-between;gap:15px;padding:18px 0;border-top:1px solid var(--line,#d8e3e9);margin-top:10px}.author-stored>div{min-width:0}.author-stored strong{font-size:13px;overflow-wrap:anywhere}.author-stored small{display:block;font-size:11px;line-height:1.8;color:var(--muted,#657b89);margin-top:5px}.author-stored>div:last-child{display:flex;gap:8px;flex-wrap:wrap}.author-stored a{display:inline-block;font-size:12px}@media(max-width:600px){.author-stored{align-items:start;flex-direction:column}.author-shelf{padding:15px}}
</style>
