<script setup>
import { computed, ref, onUnmounted } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { request } from '../api.js';
import { mutationRequester, publicationWarnings } from '../evolutionModel.js';
const props = defineProps({ endpoint: { type: String, required: true }, payload: { type: Object, required: true }, name: String, description: String });
const emit = defineEmits(['published', 'close']);
const title = ref(props.name || ''), description = ref(props.description || '');
const preview = ref(null), busy = ref(false), error = ref(''), confirmed = ref(false);
const warnings = computed(() => publicationWarnings(preview.value));
const sourceBundles = computed(() => preview.value?.root ? [
  { ...preview.value.root, role: '本次发布的组件' },
  ...(preview.value.dependencies || []).map(item => ({ ...item, role: '同时分享的依赖组件' }))
] : []);
const sourceReviewReady = computed(() => sourceBundles.value.every(bundle => Array.isArray(bundle.sourceFiles) && bundle.sourceFiles.length && bundle.sourceFiles.every(file => typeof file.text === 'string')));
const controller = new AbortController();
const post = mutationRequester(request, controller.signal);
onUnmounted(() => controller.abort());
const body = () => ({ ...props.payload, title: title.value.trim(), description: description.value.trim() });
async function inspect() {
  busy.value = true; error.value = ''; preview.value = null; confirmed.value = false;
  try { preview.value = await request(props.endpoint + '/preview', { method: 'POST', body: body(), signal: controller.signal, headers: { 'Idempotency-Key': crypto.randomUUID() } }); }
  catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message; }
  finally { busy.value = false; }
}
async function publish() {
  if (!preview.value || !confirmed.value || !sourceReviewReady.value || busy.value) return;
  busy.value = true; error.value = '';
  const payload = { ...body(), snapshotDigest: preview.value.snapshotDigest, confirmSharing: true };
  try {
    const result = await post(props.endpoint, payload);
    emit('published', result);
  } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message; }
  finally { busy.value = false; }
}
function invalidate() { preview.value = null; confirmed.value = false; }
</script>

<template>
  <section class="release-publisher" aria-label="发布到共创目录">
    <header><div><span class="release-eyebrow">EVOLVE · 分享</span><h3>让别人从你的成果出发</h3></div><button type="button" class="secondary-button" :disabled="busy" @click="emit('close')">收起</button></header>
    <p>发布后，同一平台的其他账号可以查看和复制这一版本。先检查将分享的内容，再确认发布。</p>
    <div v-if="error" role="alert" class="release-error">{{ USER_FACING_ERROR }}</div>
    <label>公开名称<input v-model="title" maxlength="128" :disabled="busy" @input="invalidate" /></label>
    <label>用途与使用说明<textarea v-model="description" rows="3" maxlength="2000" :disabled="busy" @input="invalidate" /></label>
    <button v-if="!preview" type="button" class="primary-button" :disabled="busy || !title.trim()" @click="inspect">{{ busy ? '正在检查…' : '检查发布内容' }}</button>
    <div v-else class="release-preview">
      <h4>即将分享的内容</h4>
      <p v-if="warnings.length">{{ USER_FACING_ERROR }}</p>
      <p v-if="preview.sourceReview">请逐项检查以下 {{ preview.sourceReview.fileCount }} 个文件。这里展示本次发布快照的完整内容，包含示例和依赖；分享前请移除不应公开的资料。</p>
      <p v-if="preview.dependencies?.length">同时包含 {{ preview.dependencies.length }} 个依赖组件的声明或发布文件。</p>
      <section v-for="bundle in sourceBundles" :key="bundle.sourceIdentity + bundle.contentDigest" class="release-source-bundle" :aria-label="bundle.role + '：' + bundle.name">
        <h5><span>{{ bundle.role }}</span>{{ bundle.name }}</h5>
        <p>{{ bundle.kind === 'skill' ? 'Skill · 方法' : 'Tool · 执行能力' }} · {{ bundle.sourceFiles?.length || 0 }} 个文件</p>
        <details v-for="file in bundle.sourceFiles || []" :key="file.path" class="release-source-file"><summary>{{ file.path }} <small>{{ file.size }} 字节 · 查看全文</small></summary><pre>{{ file.text }}</pre></details>
      </section>
      <p v-if="!sourceReviewReady" role="alert" class="release-error">{{ USER_FACING_ERROR }}<button type="button" class="secondary-button" :disabled="busy" @click="inspect">重新检查发布内容</button></p>
      <details><summary>查看完整发布清单</summary><pre>{{ JSON.stringify(preview, null, 2) }}</pre></details>
      <label class="release-confirm"><input v-model="confirmed" type="checkbox" :disabled="busy || !sourceReviewReady" />我已检查以上内容，确认可向平台内其他账号分享。</label>
      <button type="button" class="primary-button" :disabled="busy || !confirmed || !sourceReviewReady" @click="publish">{{ busy ? '正在发布…' : '确认发布到共创' }}</button>
    </div>
  </section>
</template>

<style scoped>
.release-publisher{margin:20px 0;padding:22px;background:var(--evolve-wash,#fff4e9);border:1px solid #ead6bc;border-radius:16px;color:var(--text)}.release-publisher header{display:flex;justify-content:space-between;align-items:start;gap:16px}.release-eyebrow{font-size:10px;letter-spacing:.15em;color:var(--evolve-ink)}.release-publisher h3{font-size:18px;margin:8px 0 0}.release-publisher>p,.release-preview p{font-size:13px;line-height:1.8;color:var(--muted)}.release-publisher label{display:grid;gap:8px;margin:14px 0;font-size:13px}.release-publisher input:not([type=checkbox]),.release-publisher textarea{width:100%;padding:10px;border:1px solid #deceb9;border-radius:8px;background:#fff;color:var(--text);font:inherit}.release-publisher textarea{resize:vertical}.release-publisher .release-confirm{display:flex;align-items:flex-start;gap:9px;line-height:1.7}.release-confirm input{margin-top:5px;flex:none}.release-preview{border-top:1px solid #ead6bc;margin-top:16px;padding-top:4px}.release-preview ul{font-size:12px;line-height:1.8;padding-left:18px;overflow-wrap:anywhere}.release-preview small{color:var(--muted);margin-left:8px}.release-preview details{font-size:12px;margin:14px 0}.release-preview pre{max-height:240px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;padding:12px;border-radius:8px}.release-error{font-size:13px;color:var(--danger);margin:12px 0;white-space:pre-wrap}
</style>

<style scoped>
.release-source-bundle{margin:18px 0;padding:16px;border:1px solid #e6d5bd;border-radius:10px;background:#fffaf3;min-width:0}.release-source-bundle h5{font-size:15px;line-height:1.6;margin:0;overflow-wrap:anywhere}.release-source-bundle h5>span{display:block;color:var(--muted);font-size:11px;font-weight:400;margin-bottom:5px}.release-source-file summary{cursor:pointer;line-height:1.8;overflow-wrap:anywhere}.release-source-file pre{max-height:420px;tab-size:4;font-size:12px;line-height:1.7}.release-source-bundle>p{margin:5px 0 12px}.release-source-file small{display:inline-block}@media(max-width:767px){.release-publisher{padding:16px}.release-source-bundle{padding:12px}}
</style>
