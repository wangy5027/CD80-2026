import { onMounted, onUnmounted, reactive } from 'vue';
import { createAssetAppsApi } from './assetAppsApi.js';
import { liveRun } from './assetAppsModel.js';

export function useAssetApps(owner, transport) {
  const controller = new AbortController(), api = createAssetAppsApi(owner, controller.signal, transport);
  const s = reactive({ items: [], legacy: [], selected: null, runs: [], activeRun: null, activeDefinition: null, busy: false, loading: false, error: '', notice: '' });
  let sequence = 0, catalogSequence = 0, timer;
  const alive = at => !controller.signal.aborted && at === sequence;
  const stopTimer = () => { clearTimeout(timer); timer = undefined; };
  async function refreshCatalog() {
    const at = ++catalogSequence;
    const [apps, legacy] = await Promise.all([api.list(), api.legacy()]);
    if (controller.signal.aborted || at !== catalogSequence) return;
    s.items = apps.items || []; s.legacy = legacy.items || [];
  }
  function schedulePoll() {
    stopTimer();
    if (!controller.signal.aborted && (s.selected?.verification?.status === 'running' || s.runs.some(liveRun) || liveRun(s.activeRun))) timer = setTimeout(poll, 1800);
  }
  async function readSelection(at) {
    const selected = s.selected;
    if (!selected) return;
    const [detail, runs] = await Promise.all([api.detail(selected.id, selected.versionId), api.runs(selected.id)]);
    if (!alive(at) || selected.id !== s.selected?.id || selected.versionId !== s.selected?.versionId) return;
    s.selected = detail; s.runs = runs.items || [];
    if (s.activeRun) s.activeRun = s.runs.find(run => run.id === s.activeRun.id) || s.activeRun;
  }
  async function poll() {
    const at = sequence;
    try { await readSelection(at); }
    catch (error) { if (alive(at) && error.name !== 'AbortError') s.error = '状态同步失败：' + error.message + '。后台运行不会因此停止。'; }
    finally { if (alive(at)) schedulePoll(); }
  }
  async function refresh() {
    s.loading = true; s.error = '';
    const at = sequence;
    try { await refreshCatalog(); if (alive(at)) await readSelection(at); }
    catch (error) { if (alive(at) && error.name !== 'AbortError') s.error = error.message; }
    finally { if (alive(at)) { s.loading = false; schedulePoll(); } }
  }
  async function select(appId, versionId) {
    if (s.busy) return;
    const at = ++sequence; stopTimer(); s.loading = true; s.error = ''; s.activeRun = null; s.activeDefinition = null;
    try {
      const [detail, runs] = await Promise.all([api.detail(appId, versionId), api.runs(appId)]);
      if (!alive(at)) return;
      s.selected = detail; s.runs = runs.items || [];
    } catch (error) { if (alive(at) && error.name !== 'AbortError') s.error = error.message; }
    finally { if (alive(at)) { s.loading = false; schedulePoll(); } }
  }
  function close() { if (s.busy) return; sequence++; stopTimer(); s.selected = null; s.runs = []; s.activeRun = null; s.activeDefinition = null; s.error = ''; s.loading = false; }
  async function observeRun(run) {
    if (s.busy) return;
    s.activeRun = run; s.activeDefinition = run.versionId === s.selected?.versionId ? s.selected.definition : null;
    if (s.activeDefinition) return;
    const at = sequence;
    try { const detail = await api.detail(run.appId, run.versionId); if (alive(at) && s.activeRun?.id === run.id) s.activeDefinition = detail.definition; }
    catch (error) { if (alive(at) && error.name !== 'AbortError') s.error = error.message; }
  }
  async function mutate(action, apply) {
    if (s.busy || controller.signal.aborted) return null;
    const at = ++sequence; stopTimer(); s.busy = true; s.error = ''; s.notice = '';
    try {
      const result = await action(api);
      if (!alive(at)) return null;
      if (apply) await apply(result);
      return alive(at) ? result : null;
    } catch (error) { if (alive(at) && error.name !== 'AbortError') s.error = error.message; return null; }
    finally { if (alive(at)) { s.busy = false; s.loading = false; schedulePoll(); } }
  }
  async function saved(result) {
    s.selected = result; s.activeRun = null; s.activeDefinition = null; s.runs = [];
    try { await refreshCatalog(); } catch (error) { if (error.name !== 'AbortError') s.error = '内容已保存，但目录刷新失败：' + error.message; }
  }
  const save = body => mutate(client => client.save(body), saved);
  const importApp = (value, bindings) => mutate(client => client.importApp(value, bindings), saved);
  const configure = bindings => mutate(client => client.configure(s.selected.id, { versionId: s.selected.versionId, revision: s.selected.revision, bindings }), result => { s.selected = result; });
  const verify = cases => mutate(client => client.verify(s.selected.id, { versionId: s.selected.versionId, revision: s.selected.revision, cases }), result => { s.selected = result; });
  const enable = enabled => mutate(client => client.enable(s.selected.id, { versionId: s.selected.versionId, revision: s.selected.revision, enabled }), result => { s.selected = result; });
  const updateRun = result => { s.activeRun = result; if (result.versionId === s.selected?.versionId) s.activeDefinition = s.selected.definition; s.runs = [result, ...s.runs.filter(run => run.id !== result.id)]; };
  const run = values => mutate(client => client.run(s.selected.id, { versionId: s.selected.versionId, ...values }), updateRun);
  const resume = (run, body) => mutate(client => client.resume(run.id, body), updateRun);
  const cancel = run => mutate(client => client.cancel(run.id, run.revision), updateRun);
  onMounted(refresh);
  onUnmounted(() => { sequence++; catalogSequence++; stopTimer(); controller.abort(); });
  return { s, api, refresh, select, close, observeRun, mutate, save, verify, enable, run, resume, cancel, importApp, configure };
}
