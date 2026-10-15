<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { request } from '../api.js';
import { allPages, dependencyBindingKey, mergeComponents, mutationRequester, preparedMutationRequester, publicationCard, publicationDate } from '../evolutionModel.js';
const emit = defineEmits(['applications', 'capability', 'app']);
const items = ref([]), query = ref(''), kind = ref('all'), mine = ref(false), loading = ref(false), busy = ref(''), errors = ref([]), notice = ref(''), copied = ref(null), details = ref(null);
const dependencyProblem = ref(null), dependencyChoices = reactive(Object.create(null));
const controller = new AbortController();
let generation = 0;
const post = mutationRequester(request, controller.signal);
const preparedCopy = preparedMutationRequester(post);
onUnmounted(() => controller.abort());
const filtered = computed(() => items.value.filter(item => (!mine.value || item.owned) && (kind.value === 'all' || (kind.value === 'applications' ? ['recipe','pipeline'].includes(item.kind) : item.kind === kind.value)) && (item.name + item.description).toLowerCase().includes(query.value.trim().toLowerCase())));
const endpoint = item => item.source === 'capability' ? '/community/capabilities' : '/evolution/publications';
async function load() {
  const at = ++generation;
  loading.value = true; errors.value = [];
  const options = { signal: controller.signal, isCurrent: () => at === generation };
  const results = await Promise.allSettled([allPages(request, '/evolution/publications', options), allPages(request, '/community/capabilities?limit=100' + (mine.value ? '&mine=true' : ''), options)]);
  if (controller.signal.aborted || at !== generation) return;
  const merged = [];
  results.forEach((result, index) => { if (result.status === 'fulfilled') merged.push(...(result.value.items || []).map(item => publicationCard(item, index ? 'capability' : 'application'))); else errors.value.push((index ? '组件目录：' : '应用目录：') + result.reason.message); });
  items.value = merged.sort((a,b) => String(b.createdAt || '').localeCompare(String(a.createdAt || ''))); loading.value = false;
}
onMounted(load);
watch(mine, load);
async function inspect(item) {
  if (busy.value) return; busy.value = item.key; errors.value = [];
  try { details.value = { ...item, detail: item.status === 'withdrawn' && item.source === 'application' ? item : await request(endpoint(item) + '/' + encodeURIComponent(item.id) + (item.source === 'capability' ? '' : '/export'), { signal: controller.signal }) }; }
  catch (cause) { if (cause.name !== 'AbortError') errors.value = [cause.message]; }
  finally { busy.value = ''; }
}
async function explainDependencies(item, cause) {
  if (dependencyProblem.value?.item.key !== item.key) Object.keys(dependencyChoices).forEach(key => delete dependencyChoices[key]);
  try {
    const components = mergeComponents((await request('/agent-components', { signal: controller.signal })).components || [], cause?.details?.candidates);
    dependencyProblem.value = { item, dependencies: (item.dependencies || []).map(dependency => ({ ...dependency, matches: components.filter(component => ['sourceIdentity','contentDigest','kind'].every(key => component.capabilityRef?.[key] === dependency[key])) })) };
  } catch { dependencyProblem.value = { item, dependencies: (item.dependencies || []).map(dependency => ({ ...dependency, matches: [] })) }; }
}
function findDependency(dependency) { kind.value = dependency.kind; mine.value = false; const candidate = items.value.find(item => item.source === 'capability' && item.contentDigest === dependency.contentDigest); query.value = candidate?.name || ''; notice.value = candidate ? `已定位「${candidate.name}」。请核对内容版本并保存个人副本，再重试应用。` : '已打开同类组件目录，请核对精确来源和内容版本。若目录中尚无对应版本，请先向作者获取组件包并在技能池中导入。'; }
const dependencyKey = dependency => dependencyBindingKey(dependency, dependencyProblem.value?.item.kind);
async function retryDependencyCopy() { const bindings = Object.fromEntries((dependencyProblem.value?.dependencies || []).filter(dependency => dependencyChoices[dependencyKey(dependency)]).map(dependency => { const [assetId,versionId] = dependencyChoices[dependencyKey(dependency)].split('@'); return [dependencyKey(dependency), { assetId, versionId }]; })); await copy(dependencyProblem.value.item, bindings); }
async function copy(item, bindings = {}) {
  if (busy.value) return; busy.value = item.key; errors.value = []; copied.value = null; notice.value = '';
  try {
    const result = await preparedCopy(endpoint(item) + '/' + encodeURIComponent(item.id) + '/copy', async () => {
      const latest = item.source === 'capability' ? await request(endpoint(item) + '/' + encodeURIComponent(item.id), { signal: controller.signal }) : item;
      const duplicate = Boolean(latest.personalCopies?.length);
      if (duplicate && !window.confirm(item.source === 'application' ? '旧版应用库中已有相同内容。仍要另存一份未验证的现代 APP 吗？' : '你的库中已有相同内容，是否再创建一个独立副本？')) return null;
      return { confirmDuplicate: duplicate, ...(item.source === 'application' && Object.keys(bindings).length ? { dependencyBindings: bindings } : {}) };
    });
    if (!result) return;
    copied.value = item.source === 'application' ? { appId: result.assetId, versionId: result.versionId, kind: item.kind } : { assetId: result.assetId, kind: item.kind };
    notice.value = item.source === 'capability' ? '已保存到我的能力池。请核对精确版本的验证要求与启用条件。' : '已保存到我的 APP，当前未验证、未启用。请打开详情检查与验证。';
    if (item.source === 'application') dependencyProblem.value = null;
    await load();
    if (!controller.signal.aborted && item.source === 'application' && copied.value?.appId && copied.value?.versionId) emit('app', copied.value);
  } catch (cause) { if (cause.name !== 'AbortError') { errors.value = [cause.message]; if (item.source === 'application' && (item.dependencies?.length || /DEPENDENCY|dependency|CAPABILITY|capability/.test(cause.code || ''))) await explainDependencies(item, cause); } }
  finally { busy.value = ''; }
}
async function withdraw(item) {
  if (busy.value || !window.confirm('从共创目录撤回这一发布版本？已被他人复制的个人副本会保留。')) return;
  busy.value = item.key; errors.value = [];
  try { await post(endpoint(item) + '/' + encodeURIComponent(item.id) + '/withdraw', { revision: item.revision }); details.value = null; await load(); notice.value = '已撤回发布，已有个人副本不受影响。'; }
  catch (cause) { if (cause.name !== 'AbortError') errors.value = [cause.message]; }
  finally { busy.value = ''; }
}
function openCopy() { if (copied.value?.appId && copied.value?.versionId) emit('app', copied.value); else if (copied.value?.assetId) emit('capability', { assetId: copied.value.assetId }); else emit('applications'); }
</script>

<template>
  <div class="community-page">
    <header class="community-hero"><div><span>EVOLVE / 共创</span><h1>每个成果，都是新的起点。</h1><p>从同一平台用户分享的应用与组件出发，复制、改编，再把你的方法分享回来。</p></div><button type="button" class="secondary-button" @click="emit('applications')">我的应用与发布</button></header>
    <div class="community-loop" aria-label="复用流程"><span>发现可用成果</span><i>→</i><span>保存自己的副本</span><i>→</i><span>验证与改编</span><i>→</i><span>分享新版本</span></div>
    <p v-if="errors.length" class="community-error" role="alert">{{ USER_FACING_ERROR }}</p>
    <div v-if="notice" class="community-notice" role="status">{{ notice }} <button v-if="copied" type="button" @click="openCopy">打开我的副本 →</button></div>
    <section v-if="dependencyProblem" class="community-dependency-help"><h2>先准备「{{ dependencyProblem.item.name }}」的组件</h2><p>应用需要以下精确版本。引入并选择个人副本后可保存应用；实际运行前仍需验证并启用组件。</p><div v-for="(dependency,index) in dependencyProblem.dependencies" :key="dependencyKey(dependency) || index" class="community-dependency-row"><div><strong>{{ dependency.matches[0]?.name || dependency.sourceIdentity }}</strong><small>{{ dependency.kind }} · {{ dependency.sourceIdentity }} · 内容 {{ dependency.contentDigest?.slice(0,16) }}</small><span>{{ dependency.matches.length ? dependency.matches.some(item => item.available) ? '已有可用版本；多个副本时请明确选择' : '已引入，尚需验证或启用' : '尚未引入这一内容版本' }}</span></div><div class="community-actions"><button type="button" class="secondary-button" :disabled="!!busy" @click="findDependency(dependency)">去共创查找组件</button><button type="button" class="secondary-button" :disabled="!!busy" @click="emit('capability', { assetId: dependency.matches[0]?.assetId || '' })">去技能池验证启用</button><select v-if="dependency.matches.length > 1" v-model="dependencyChoices[dependencyKey(dependency)]" :disabled="!!busy" :aria-label="dependency.sourceIdentity + ' 的副本'"><option :value="undefined">选择个人副本</option><option v-for="item in dependency.matches" :key="item.assetId + item.versionId" :value="item.assetId + '@' + item.versionId">{{ item.name }} · {{ item.assetId.slice(-6) }}</option></select></div></div><button class="primary-button" :disabled="!!busy" @click="retryDependencyCopy">完成准备，重试复制</button></section>
    <div class="community-toolbar"><div class="community-tabs" role="tablist" aria-label="成果类型"><button v-for="tab in [{id:'all',name:'全部成果'},{id:'applications',name:'应用'},{id:'skill',name:'Skills'},{id:'tool',name:'Tools'}]" :key="tab.id" type="button" role="tab" :aria-selected="kind === tab.id" :class="{active:kind === tab.id}" @click="kind = tab.id">{{ tab.name }}</button></div><div class="community-filters"><label><input v-model="mine" type="checkbox" />我的发布</label><input v-model="query" type="search" aria-label="搜索共创成果" placeholder="搜索名称或用途" /><button type="button" class="secondary-button" :disabled="loading || !!busy" @click="load">刷新</button></div></div>
    <p v-if="loading" class="community-muted" role="status">正在读取共创目录…</p>
    <div v-else-if="!filtered.length" class="community-empty"><span>从一次分享开始</span><h2>{{ mine ? '你还没有发布成果' : '这里正在等待新的起点' }}</h2><p>在“我的应用”或组件详情选择发布。系统会先展示分享清单，由你确认这一版本的内容。</p><button class="primary-button" @click="emit('applications')">前往我的应用</button></div>
    <div v-else class="community-grid"><article v-for="item in filtered" :key="item.key" class="community-card" :class="item.kind"><header><span class="community-kind">{{ item.label }}</span><span v-if="item.status === 'withdrawn'" class="community-owner">已撤回</span><span v-else-if="item.owned" class="community-owner">我的发布</span></header><h2>{{ item.name }}</h2><p>{{ item.description || '作者尚未添加用途说明，可查看发布详情。' }}</p><div class="community-meta"><span>{{ item.publisher?.name || item.publisherName || '平台成员' }}</span><span>{{ publicationDate(item.createdAt) }}</span></div><p v-if="item.personalCopies?.length" class="community-added">已添加 {{ item.personalCopies.length }} 个副本</p><div class="community-actions"><button type="button" class="primary-button" :disabled="!!busy || item.status === 'withdrawn'" @click="copy(item)">{{ busy === item.key ? '请稍候…' : '保存个人副本' }}</button><button type="button" class="secondary-button" :disabled="!!busy" @click="inspect(item)">详情</button><button v-if="item.owned && item.status !== 'withdrawn'" type="button" class="community-text" :disabled="!!busy" @click="withdraw(item)">撤回</button></div></article></div>
    <section v-if="details" class="community-detail"><header><div><span class="community-kind">{{ details.label }}</span><h2>{{ details.name }}</h2></div><button class="secondary-button" @click="details = null">关闭详情</button></header><p>{{ details.description }}</p><p class="community-muted">复制后拥有独立个人副本；原发布的凭据、输入文件和历史运行授权不会随之转移。</p><details><summary>版本、依赖与发布清单</summary><pre>{{ JSON.stringify(details.detail, null, 2) }}</pre></details><a v-if="details.source === 'application' && details.status !== 'withdrawn'" class="community-text" :href="'/desktop/api/evolution/publications/' + encodeURIComponent(details.id) + '/export'" download>下载应用包</a></section>
  </div>
</template>

<style scoped>
.community-page{padding:44px 36px 64px;max-width:1280px;width:100%;margin:0 auto}.community-hero{display:flex;align-items:end;justify-content:space-between;gap:30px}.community-hero>div>span{font-size:11px;letter-spacing:.16em;color:var(--evolve-ink)}.community-hero h1{font-size:clamp(26px,3vw,40px);font-weight:600;letter-spacing:-.025em;margin:14px 0}.community-hero p{max-width:640px;font-size:15px;line-height:1.9;color:var(--muted);margin:0}.community-hero>button{flex:none}.community-loop{display:flex;flex-wrap:wrap;gap:18px;align-items:center;padding:22px 0 30px;margin-top:10px;border-bottom:1px solid var(--line);font-size:12px;color:var(--evolve-ink)}.community-loop i{font-style:normal;color:#bbaa95}.community-toolbar{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap;padding:26px 0}.community-tabs,.community-filters{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.community-tabs button{background:transparent;border:0;border-radius:8px;padding:9px 13px;color:var(--muted);font-size:13px}.community-tabs button.active{background:var(--evolve-wash);color:var(--evolve-ink)}.community-filters{font-size:12px;color:var(--muted)}.community-filters label{display:flex;gap:6px;align-items:center}.community-filters input[type=search]{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:9px;max-width:190px}.community-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(290px,100%),1fr));gap:19px}.community-card{display:flex;flex-direction:column;min-width:0;padding:24px;background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:0 8px 28px #1b394e04}.community-card header{display:flex;justify-content:space-between;gap:8px}.community-kind{font-size:10px;letter-spacing:.04em;color:var(--evolve-ink);background:var(--evolve-wash);border-radius:5px;padding:5px 8px}.skill .community-kind{background:var(--explore-wash);color:var(--explore-ink)}.tool .community-kind{background:var(--build-wash);color:var(--build-ink)}.community-owner{font-size:10px;color:var(--muted);padding:5px 0}.community-card h2{font-size:20px;font-weight:600;line-height:1.5;margin:18px 0 10px}.community-card>p{font-size:13px;line-height:1.9;color:var(--muted);margin:0 0 20px;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}.community-meta{display:flex;justify-content:space-between;gap:8px;font-size:10px;color:var(--dim);margin:auto 0 18px;padding-top:8px}.community-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.community-actions button{font-size:12px}.community-text{background:transparent;border:0;font-size:12px;color:var(--evolve-ink);padding:6px 0;text-decoration:underline;cursor:pointer}.community-card .community-added{font-size:11px;color:var(--success-ink);margin-bottom:10px}.community-error,.community-notice{font-size:13px;line-height:1.8;padding:14px 17px;border-radius:10px}.community-error{background:var(--error-wash);color:var(--danger)}.community-notice{background:var(--success-wash);color:var(--success-ink);margin-top:20px}.community-notice button{background:transparent;border:0;text-decoration:underline;color:inherit;font-size:12px}.community-empty{text-align:center;padding:60px 24px;border:1px dashed #dacbb9;border-radius:18px;background:#fffdf9}.community-empty>span{font-size:11px;color:var(--evolve-ink);letter-spacing:.14em}.community-empty h2{font-size:23px;font-weight:500}.community-empty p{font-size:13px;line-height:1.9;color:var(--muted);max-width:470px;margin:18px auto 24px}.community-muted{font-size:12px;line-height:1.9;color:var(--muted)}.community-detail{border:1px solid var(--line);border-radius:18px;padding:24px;margin-top:24px;background:var(--surface)}.community-detail header{display:flex;justify-content:space-between;align-items:start;gap:18px}.community-detail h2{font-size:21px}.community-detail>p{font-size:13px;line-height:1.8}.community-detail details{font-size:12px;margin:18px 0}.community-detail pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto;padding:16px;background:var(--surface-2);border-radius:8px}@media(max-width:767px){.community-page{padding:76px 18px 34px}.community-hero{align-items:start;flex-direction:column;gap:17px}.community-loop{gap:9px;font-size:11px}.community-toolbar{align-items:stretch}.community-filters input[type=search]{flex:1;min-width:100px}}
</style>

<style scoped>
.community-dependency-help{padding:20px 22px;margin:20px 0;border:1px solid #e8d9c5;border-radius:14px;background:#fffbf5}.community-dependency-help h2{font-size:18px;font-weight:600;margin:0 0 10px}.community-dependency-help>p{font-size:13px;color:var(--muted);line-height:1.8}.community-dependency-row{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:15px 0;border-top:1px solid #eee3d6}.community-dependency-row>div:first-child{min-width:0}.community-dependency-row strong{font-size:13px;overflow-wrap:anywhere}.community-dependency-row small{display:block;font-size:10px;line-height:1.8;color:var(--muted);overflow-wrap:anywhere}.community-dependency-row span{display:block;font-size:12px;color:var(--evolve-ink);line-height:1.8}.community-dependency-row select{border:1px solid var(--line);border-radius:7px;background:var(--surface);padding:9px;font-size:12px;max-width:100%}.community-dependency-help>.primary-button{margin-top:8px}@media(max-width:767px){.community-dependency-row{align-items:start;flex-direction:column}.community-dependency-help{padding:18px}}
</style>
