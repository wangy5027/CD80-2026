<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { request } from '../api.js';
import { dependencyBindingKey, mergeComponents, mutationRequester, recipeBody, selectedRecipeComponents, templateParameters } from '../evolutionModel.js';
import ReleasePublisher from './ReleasePublisher.vue';
const props = defineProps({ files: { type: Array, default: () => [] }, captureRunId: String, unifiedAuthoring: Boolean });
const emit = defineEmits(['open-task', 'imported', 'captured', 'community', 'author']);
const items = ref([]), loading = ref(false), busy = ref(false), error = ref(''), notice = ref('');
const editor = ref(false), forkOf = ref(null), selected = ref(null), publishing = ref(null), picker = ref(null);
const input = reactive(Object.create(null)), fileIds = ref([]), warnings = ref([]);
const importPackage = ref(null), importComponents = ref([]), dependencyBindings = reactive(Object.create(null));
const editorComponents = ref([]);
const form = reactive({ name: '', description: '', goalTemplate: '', constraints: '', capabilityRefs: [], sourceRunId: '' });
const parameters = computed(() => templateParameters(form.goalTemplate));
const selectedComponents = computed(() => selectedRecipeComponents(form.capabilityRefs, editorComponents.value));
function removeComponent(assetId) { form.capabilityRefs = form.capabilityRefs.filter(item => item.assetId !== assetId); }
function replaceComponent(item) { form.capabilityRefs = form.capabilityRefs.map(reference => reference.assetId === item.assetId ? { assetId: item.assetId, versionId: item.replacement.versionId } : reference); }
const componentSelection = computed({ get: () => form.capabilityRefs.map(item => item.assetId), set(ids) { form.capabilityRefs = ids.map(id => form.capabilityRefs.find(item => item.assetId === id) || editorComponents.value.find(item => item.assetId === id)).filter(Boolean).map(({ assetId, versionId }) => ({ assetId, versionId })); } });
const controller = new AbortController();
onUnmounted(() => controller.abort());
const mutate = mutationRequester(request, controller.signal);
const post = (path, body) => mutate('/evolution' + path, body);
async function act(fn) { if (busy.value) return; busy.value = true; error.value = ''; notice.value = ''; try { await fn(); } catch (cause) { if (cause.name !== 'AbortError') { error.value = cause.message; if (cause.details?.candidates) importComponents.value = mergeComponents(importComponents.value, cause.details.candidates); } } finally { busy.value = false; } }
async function load() { loading.value = true; try { items.value = (await request('/evolution/recipes', { signal: controller.signal })).items || []; } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message; } finally { loading.value = false; } }
function edit(value = {}, fork = null) {
  if (props.unifiedAuthoring) { const source = fork || value; emit('author', { kind: 'app', intent: source.id ? 'upgrade' : 'create', instruction: source.id ? '基于旧研究应用整理完整实现，保留业务目标与约束。' : '', sourceRefs: source.id ? [{ kind: 'asset_version', reference: { legacy: { kind: 'recipe', id: source.id } } }] : [] }); return; }
  Object.assign(form, { name: value.name || '', description: value.description || '', goalTemplate: value.goalTemplate || '', constraints: (value.constraints || []).join('\n'), capabilityRefs: value.capabilityRefs || [], sourceRunId: value.sourceRunId || '' });
  forkOf.value = fork; editor.value = true; selected.value = null; publishing.value = null;
  request('/agent-components', { signal: controller.signal }).then(result => { if (!controller.signal.aborted) editorComponents.value = result.components || []; }).catch(cause => { if (cause.name !== 'AbortError') error.value = cause.message; });
}
async function capture() { if (props.unifiedAuthoring) { emit('author', { kind: 'app', intent: 'capture', sourceRefs: [{ kind: 'sub_agent_run', reference: { runId: props.captureRunId } }] }); emit('captured'); return; } await act(async () => { const result = await post('/captures/preview', { runId: props.captureRunId, kind: 'recipe' }); edit(result.recipe); warnings.value = result.warnings || []; }); }
function chooseImport() { if (props.unifiedAuthoring) emit('author', { kind: 'app', action: 'import' }); else picker.value?.click(); }
onMounted(async () => { await load(); if (props.captureRunId && !controller.signal.aborted) await capture(); });
watch(() => props.captureRunId, value => { if (value) capture(); });
async function save() {
  await act(async () => { const body = recipeBody(form); const result = await post(forkOf.value ? `/recipes/${encodeURIComponent(forkOf.value.id)}/fork` : '/recipes', body); editor.value = false; await load(); notice.value = `已保存「${result.name}」。每次运行都会按新输入重新取数与分析。`; emit('captured'); });
}
function select(item) { selected.value = item; editor.value = false; publishing.value = null; Object.keys(input).forEach(key => delete input[key]); (item.parameters || []).forEach(key => { input[key] = ''; }); fileIds.value = []; }
async function run() { await act(async () => { const item = selected.value; const result = await post(`/recipes/${encodeURIComponent(item.id)}/run`, { input: { ...input }, fileIds: [...fileIds.value] }); const id = result.conversationId || result.taskId || result.run?.conversationId; if (id) emit('open-task', id); else notice.value = '已发起运行，可在最近任务中查看。'; }); }
async function importFile(event) {
  const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
  await act(async () => {
    if (file.size > 2 * 1024 * 1024) throw new Error('应用包超过 2 MiB，请检查包内容。');
    let value; try { value = JSON.parse(await file.text()); } catch { throw new Error('请选择有效的 JSON 应用包。'); }
    if (value?.format !== 'infinity-application' || !['recipe', 'pipeline'].includes(value.kind) || !value.definition || typeof value.definition !== 'object') throw new Error('请选择兼容的研究应用或流程应用包，可先下载外部开发样例。');
    importPackage.value = value; importComponents.value = []; Object.keys(dependencyBindings).forEach(key => delete dependencyBindings[key]);
    if (value.dependencies?.length) importComponents.value = (await request('/agent-components', { signal: controller.signal })).components || [];
    notice.value = '已读取应用包，尚未保存或运行。请核对下方定义与依赖，再导入。';
  });
}
function dependencyChoices(dependency) { return importComponents.value.filter(item => ['sourceIdentity', 'contentDigest', 'kind'].every(key => item.capabilityRef?.[key] === dependency[key])); }
function bindingKey(dependency) { return dependencyBindingKey(dependency, importPackage.value?.kind); }
async function confirmImport() { await act(async () => {
  const value = importPackage.value;
  const bindings = Object.fromEntries((value.dependencies || []).filter(dep => dependencyBindings[bindingKey(dep)]).map(dep => { const [assetId, versionId] = dependencyBindings[bindingKey(dep)].split('@'); return [bindingKey(dep), { assetId, versionId }]; }));
  if (value.kind === 'recipe') { const result = await post('/recipes/import', { package: value, dependencyBindings: bindings }); await load(); notice.value = `已导入「${result.name}」，请核对依赖后运行。`; }
  else {
    const result = await mutate('/agents/import', { package: value, dependencyBindings: bindings }, { bodyKey: true }); notice.value = `已导入「${result.spec?.name || '流程应用'}」。结构检查通过，实际效果需运行验证。`; emit('imported');
  }
  importPackage.value = null;
}); }
</script>

<template>
  <section class="recipe-library">
    <header class="recipe-header"><div><span class="recipe-eyebrow">MY APPLICATIONS</span><h2>把研究方法保存下来</h2><p>以目标、约束和组件组成研究应用，换一个标的或区间，再完成一次新的分析。</p></div><div class="recipe-actions"><button type="button" class="secondary-button" :disabled="busy" @click="chooseImport">导入应用包</button><button type="button" class="secondary-button" :disabled="busy" @click="edit()">新建研究应用</button><button type="button" class="secondary-button" @click="emit('community')">浏览共创</button></div></header>
    <p class="recipe-developer-link"><a href="/desktop/api/evolution/developer-kit" download>外部开发指南与样例 ↓</a><span>用其他开发工具编写兼容应用，再通过应用包导入平台。</span></p>
    <input ref="picker" type="file" accept=".json,application/json" hidden @change="importFile" />
    <p v-if="error" class="recipe-error" role="alert">{{ USER_FACING_ERROR }}</p><p v-if="notice" class="recipe-notice" role="status">{{ notice }}</p>
    <section v-if="importPackage" class="recipe-editor"><div class="recipe-section-head"><h3>导入「{{ importPackage.definition.name }}」</h3><button type="button" class="secondary-button" :disabled="busy" @click="importPackage = null">取消导入</button></div><p class="recipe-hint">{{ importPackage.kind === 'recipe' ? '研究应用' : '流程应用' }} · {{ importPackage.definition.description || '暂无用途说明' }}</p><p class="recipe-hint">导入会检查兼容性并保存个人副本。原始凭据、输入文件与运行授权不会继承。</p><div v-if="importPackage.dependencies?.length"><h4>依赖组件</h4><label v-for="(dependency,index) in importPackage.dependencies" :key="bindingKey(dependency) || index">{{ dependency.sourceIdentity }}<small class="recipe-hint">精确内容：{{ dependency.contentDigest?.slice(0,16) }}</small><select v-model="dependencyBindings[bindingKey(dependency)]" :disabled="busy"><option :value="undefined">由平台匹配精确版本</option><option v-for="item in dependencyChoices(dependency)" :key="item.assetId + item.versionId" :value="item.assetId + '@' + item.versionId">{{ item.name }} · {{ item.assetId.slice(-6) }}{{ item.available ? '' : '（需验证或启用）' }}</option></select></label><p class="recipe-hint">缺少依赖时，请先从技能池或共创目录引入对应组件，再重试导入。多个相同副本可在上方明确选择。</p></div><details><summary>查看应用定义</summary><pre class="recipe-package-preview">{{ JSON.stringify(importPackage.definition, null, 2) }}</pre></details><button type="button" class="primary-button" :disabled="busy" @click="confirmImport">{{ busy ? '正在导入…' : '导入并保存（不运行）' }}</button></section>
    <form v-if="editor" class="recipe-editor" @submit.prevent="save">
      <div class="recipe-section-head"><h3>{{ forkOf ? '改编为新的研究应用' : '保存为研究应用' }}</h3><button type="button" class="secondary-button" :disabled="busy" @click="editor = false; emit('captured')">收起</button></div>
      <p v-if="warnings.length" class="recipe-hint">{{ USER_FACING_ERROR }}</p>
      <label>应用名称<input v-model="form.name" :disabled="busy" required maxlength="128" placeholder="例如：个股价格与成交分析" /></label>
      <label>用途说明<textarea v-model="form.description" :disabled="busy" rows="2" placeholder="适合什么问题，会产生什么结果" /></label>
      <label>可重复使用的任务模板<textarea v-model="form.goalTemplate" :disabled="busy" rows="5" required placeholder="例如：分析 {{symbol}} 在 {{start_date}} 至 {{end_date}} 的价格和成交变化，提供图表并解释数据限制。" /></label>
      <p class="recipe-hint">用双花括号包住可变参数，如 <code v-text="'{{symbol}}'" />。每次运行时单独填写。</p>
      <p class="recipe-hint">中文示例：<code v-text="'分析 {{证券}} 从 {{起始日期}} 到 {{结束日期}} 的价格与成交变化。'" /> 旧文件不会带入，新一次运行需重新选择并授权。</p>
      <div v-if="parameters.length" class="recipe-parameters"><span v-for="key in parameters" :key="key">{{ key }}</span></div>
      <label>必须遵守的规则（每行一条）<textarea v-model="form.constraints" :disabled="busy" rows="3" placeholder="例如：先检查数据覆盖；图表必须标注数据来源；缺数据时说明限制。" /></label>
      <p v-if="form.capabilityRefs.length" class="recipe-hint">保留 {{ form.capabilityRefs.length }} 个组件的精确版本引用。运行时会重新检查你的访问与启用状态。</p>
      <div v-if="selectedComponents.length" class="recipe-selected-components" aria-label="已选组件"><div v-for="item in selectedComponents" :key="item.assetId" class="recipe-selected-component" :class="{ missing: item.missing }"><div><strong>{{ item.name }}</strong><small>{{ item.assetId }} · {{ item.versionId }}</small><p>{{ item.missing ? USER_FACING_ERROR : item.status }}</p></div><div class="recipe-actions"><button v-if="item.replacement" type="button" class="secondary-button" :disabled="busy" @click="replaceComponent(item)">换为当前可用版本</button><button type="button" class="secondary-button" :disabled="busy" :aria-label="'取消组件 ' + item.assetId" @click="removeComponent(item.assetId)">取消选择</button></div></div></div>
      <details><summary>组合我的组件 · {{ componentSelection.length }} 项已选</summary><p class="recipe-hint">最多选择 8 项已经验证并启用的组件。未选择时，主助手会按任务需要发现你可用的能力。</p><p v-if="!editorComponents.length" class="recipe-hint">还没有可用组件，可以先在技能池中添加。</p><label v-for="item in editorComponents" :key="item.assetId" class="recipe-file"><input v-model="componentSelection" type="checkbox" :value="item.assetId" :disabled="busy || ((!item.available || componentSelection.length >= 8) && !componentSelection.includes(item.assetId))" /><span>{{ item.name }}<small class="recipe-hint"> · {{ item.kind }}{{ item.available ? '' : ' · ' + USER_FACING_ERROR }}</small></span></label></details>
      <button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在保存…' : '审阅并保存应用' }}</button>
    </form>
    <p v-if="loading" class="recipe-hint" role="status">正在读取研究应用…</p>
    <div v-if="!editor && items.length" class="recipe-grid"><article v-for="item in items" :key="item.id" class="recipe-card"><span class="recipe-type">研究应用</span><h3>{{ item.name }}</h3><p>{{ item.description || item.goalTemplate }}</p><small>{{ item.parameters?.length || 0 }} 个可变参数 · {{ item.capabilityRefs?.length || 0 }} 个组件</small><div class="recipe-actions"><button class="primary-button" :disabled="busy" @click="select(item)">填写参数并运行</button><button class="secondary-button" :disabled="busy" @click="edit({ ...item, name: item.name + ' · 改编' }, item)">改编</button><a class="recipe-link" :href="'/desktop/api/evolution/recipes/' + encodeURIComponent(item.id) + '/export'" download>导出包</a><button class="recipe-link" :disabled="busy" @click="publishing = item; selected = null">发布</button></div></article></div>
    <p v-if="!loading && !items.length && !editor" class="recipe-empty">完成一次研究后，在结果卡选择“保存为应用”；也可以导入外部应用包，或从共创目录复制起点。</p>
    <form v-if="selected" class="recipe-editor" @submit.prevent="run"><div class="recipe-section-head"><h3>{{ selected.name }}</h3><button type="button" class="secondary-button" :disabled="busy" @click="selected = null">关闭</button></div><p class="recipe-hint">新输入会触发新的研究任务，历史结果与历史文件授权不会自动沿用。</p><label v-for="key in selected.parameters" :key="key">{{ key }}<input v-model="input[key]" :disabled="busy" required :placeholder="'填写 ' + key" /></label><details v-if="files.length"><summary>授权本次使用的文件（可选）</summary><label v-for="file in files" :key="file.id" class="recipe-file"><input v-model="fileIds" :disabled="busy" type="checkbox" :value="file.id" />{{ file.name }}</label></details><button type="submit" class="primary-button" :disabled="busy">{{ busy ? '正在发起…' : '按这些输入运行' }}</button></form>
    <ReleasePublisher v-if="publishing" :key="publishing.id" endpoint="/evolution/publications" :payload="{ kind: 'recipe', recipeId: publishing.id }" :name="publishing.name" :description="publishing.description" @close="publishing = null" @published="publishing = null; notice = '已发布到共创目录，其他平台账号可以复制这一版本。'" />
  </section>
</template>

<style scoped>
.recipe-library{max-width:960px;margin:0 auto 32px}.recipe-header{display:flex;justify-content:space-between;align-items:start;gap:22px;padding:8px 0 22px;border-bottom:1px solid var(--line)}.recipe-eyebrow{font-size:10px;letter-spacing:.16em;color:var(--build-ink)}.recipe-header h2{font-size:23px;font-weight:600;margin:9px 0}.recipe-header p,.recipe-card p{font-size:13px;line-height:1.8;color:var(--muted)}.recipe-header p{max-width:540px;margin:0}.recipe-actions{display:flex;gap:9px;flex-wrap:wrap;align-items:center}.recipe-header>.recipe-actions{justify-content:flex-end;max-width:285px}.recipe-actions button,.recipe-actions a{font-size:12px}.recipe-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:16px;margin-top:22px}.recipe-card{background:var(--surface);border:1px solid var(--line);border-top:3px solid #bfd6e9;padding:23px;border-radius:16px;min-width:0}.recipe-type{font-size:10px;color:var(--build-ink);background:var(--build-wash);padding:4px 7px;border-radius:5px}.recipe-card h3{font-size:18px;margin:14px 0 9px}.recipe-card p{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;min-height:48px}.recipe-card small{display:block;font-size:11px;color:var(--muted);margin-bottom:18px}.recipe-link{background:transparent;border:0;color:var(--build-ink);padding:7px 3px;text-decoration:underline;cursor:pointer}.recipe-editor{padding:24px;margin:24px 0;background:var(--surface);border:1px solid #c9ddeb;border-radius:18px}.recipe-section-head{display:flex;justify-content:space-between;gap:16px;align-items:center}.recipe-section-head h3{margin:0;font-size:19px}.recipe-editor label{display:grid;gap:8px;font-size:13px;margin:16px 0}.recipe-editor input:not([type=checkbox]),.recipe-editor textarea{width:100%;font:inherit;padding:11px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2);color:var(--text)}.recipe-editor textarea{resize:vertical;line-height:1.8}.recipe-hint{font-size:12px;line-height:1.8;color:var(--muted)}.recipe-parameters{display:flex;gap:8px;flex-wrap:wrap}.recipe-parameters span{font-size:11px;background:var(--build-wash);color:var(--build-ink);padding:5px 9px;border-radius:5px}.recipe-editor .recipe-file{display:flex;align-items:center;gap:9px}.recipe-editor details{font-size:12px;margin:18px 0}.recipe-empty{padding:24px 0;font-size:13px;line-height:1.8;color:var(--muted)}.recipe-error,.recipe-notice{padding:13px 16px;font-size:13px;line-height:1.8;border-radius:10px;white-space:pre-wrap}.recipe-error{background:var(--error-wash);color:var(--danger)}.recipe-notice{background:var(--success-wash);color:var(--success-ink)}@media(max-width:700px){.recipe-header{flex-direction:column}.recipe-header>.recipe-actions{justify-content:flex-start;max-width:none}.recipe-editor{padding:18px}}
</style>

<style scoped>
.recipe-developer-link{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;font-size:12px;line-height:1.8;padding:14px 0;color:var(--muted)}.recipe-developer-link a{color:var(--build-ink);text-underline-offset:3px}.recipe-editor select{width:100%;font:inherit;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2);color:var(--text)}.recipe-package-preview{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:300px;overflow:auto;padding:15px;background:var(--surface-2);border-radius:9px}
</style>

<style scoped>
.recipe-selected-components{margin:18px 0;border:1px solid var(--line);border-radius:10px;overflow:hidden}.recipe-selected-component{display:flex;justify-content:space-between;gap:14px;padding:15px;border-bottom:1px solid var(--line);background:var(--surface-2)}.recipe-selected-component:last-child{border-bottom:0}.recipe-selected-component>div:first-child{min-width:0}.recipe-selected-component strong{font-size:13px}.recipe-selected-component small{display:block;font-size:10px;color:var(--muted);overflow-wrap:anywhere;margin-top:6px}.recipe-selected-component p{font-size:12px;line-height:1.8;color:var(--muted);margin:7px 0 0}.recipe-selected-component.missing{background:var(--error-wash)}.recipe-selected-component.missing p{color:var(--danger)}.recipe-selected-component .recipe-actions{flex:none}@media(max-width:700px){.recipe-selected-component{flex-direction:column}}
</style>
