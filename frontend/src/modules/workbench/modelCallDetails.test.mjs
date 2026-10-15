import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { setImmediate } from 'node:timers/promises';
import { createRenderer, nextTick, reactive } from 'vue';
import { compileScript, parse } from '@vue/compiler-sfc';
import { component, renderer as componentRenderer, hostNode, content as componentContent } from './assetComponentHarness.mjs';

// Compile and render the actual SFC with Vue's lightweight host, without a browser or dist build.
const filename = new URL('./components/ModelCallDetails.vue', import.meta.url);
const { descriptor } = parse(await readFile(filename, 'utf8'), { filename: filename.pathname });
const vueUrl = pathToFileURL(join(dirname(createRequire(import.meta.url).resolve('vue/package.json')), 'index.mjs')).href;
const code = compileScript(descriptor, { id: 'trace-detail-test', inlineTemplate: true }).content
  .replace(/from (['"])vue\1/g, 'from ' + JSON.stringify(vueUrl))
  .replace(/from (['"])\.\.\/([^'"]+\.js)\1/g, (_match, _quote, path) => 'from ' + JSON.stringify(new URL('./' + path, import.meta.url).href));
const { default: ModelCallDetails } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null });
const renderer = createRenderer({
  createElement: type => node(type), createText: text => node('#text', text), createComment: text => node('#comment', text),
  setText: (target, text) => { target.text = text; }, setElementText: (target, text) => { target.text = text; target.children = []; },
  patchProp: (target, key, _previous, value) => { target.props[key] = value; },
  parentNode: target => target.parent,
  nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] || null,
  insert(target, parent, anchor = null) {
    if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1);
    target.parent = parent;
    parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, target);
  },
  remove(target) { if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1); target.parent = null; }
});
function find(target, matches) {
  if (matches(target)) return target;
  for (const child of target.children) { const result = find(child, matches); if (result) return result; }
}
function content(target) { return target.text + target.children.map(content).join(''); }
const flush = async () => { await setImmediate(); await nextTick(); };

test('model detail loads only when expanded, retries errors, shows bounded text, and explains missing historical logs', async () => {
  const originalFetch = globalThis.fetch, root = node('root'); let requests = 0, app;
  globalThis.fetch = async () => ++requests === 1
    ? { ok: false, status: 503, json: async () => ({ error: '暂时不可读' }) }
    : { ok: true, json: async () => ({ request: { messages: [{ role: 'user', content: 'business request' }] }, response: { text: '<script>globalThis.tracePayloadExecuted = true</script>' }, recordInfo: { truncated: true } }) };
  try {
    const traceUrl = '/desktop/api/sub-agent-runs/r/model-calls/c';
    app = renderer.createApp(ModelCallDetails, { runId: 'r', call: { id: 'c', status: 'failed', traceUrl, traceAvailable: true, errorCode: 'MODEL_INVALID_JSON' } });
    app.mount(root);
    assert.equal(requests, 0); assert.doesNotMatch(content(root), /business request|已隐藏凭据/);
    find(root, target => target.type === 'details').props.onToggle({ target: { open: true } }); await flush();
    assert.equal(requests, 1); assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /暂时不可读/);
    find(root, target => target.type === 'button').props.onClick(); await flush();
    assert.equal(requests, 2); assert.match(content(root), /business request/); assert.match(content(root), /不展示系统或开发者提示词/);
    assert.equal(find(root, target => target.type === 'a'), undefined);
    assert.equal(find(root, target => target.props['aria-label'] === '模型请求与响应 JSON'), undefined);
    assert.equal(find(root, target => target.props['aria-label'] === '模型输出节选'), undefined, '失败的模型输出不能作为正常结果展示');
    assert.equal(find(root, target => target.type === 'script' || 'innerHTML' in target.props), undefined, 'Model output must stay a text node, never interpreted markup');
    app.unmount();
    app = renderer.createApp(ModelCallDetails, { runId: 'r', call: { id: 'legacy', status: 'failed' } }); app.mount(root);
    find(root, target => target.type === 'details').props.onToggle({ target: { open: true } }); await flush();
    assert.match(content(root), /历史记录未保存请求详情/); assert.equal(requests, 2);
    assert.equal(find(root, target => target.type === 'a'), undefined);
  } finally { app?.unmount(); globalThis.fetch = originalFetch; }
});

test('an expanded in-flight request refreshes when completed, while closed records do not prefetch updates', async () => {
  const originalFetch = globalThis.fetch, root = node('root'); let requests = 0, app;
  globalThis.fetch = async () => ({ ok: true, json: async () => ++requests === 1
    ? { status: 'running', detail: { value: { request: { messages: [] } }, truncated: false } }
    : { status: 'succeeded', detail: { value: { request: { messages: [] }, response: { text: 'final response' } }, truncated: false }, platformDecision: { decision: 'accept' } } });
  try {
    const call = reactive({ id: 'c', status: 'running', traceUrl: '/desktop/api/sub-agent-runs/r/model-calls/c', traceAvailable: true, validationErrors: [] });
    app = renderer.createApp(ModelCallDetails, { runId: 'r', call }); app.mount(root);
    const details = find(root, target => target.type === 'details');
    details.props.onToggle({ target: { open: true } }); await flush();
    assert.equal(requests, 1); assert.doesNotMatch(content(root), /final response/);
    call.validationErrors = []; await flush();
    assert.equal(requests, 1, 'Equivalent polling snapshots must not trigger additional detail requests');
    call.status = 'succeeded'; call.completedAt = '2026-10-14T10:00:00Z'; await flush();
    assert.equal(requests, 2); assert.match(content(root), /final response/); assert.doesNotMatch(content(root), /MODEL_INVALID_JSON/);
    details.props.onToggle({ target: { open: false } }); await flush();
    call.finalDecision = 'accept'; await flush();
    assert.equal(requests, 2, 'Collapsed diagnostic records must not fetch when the snapshot changes');
  } finally { app?.unmount(); globalThis.fetch = originalFetch; }
});

test('model detail does not render privileged prompt text from nested input, output, errors or raw fragments', async () => {
  const originalFetch = globalThis.fetch, root = node('root'); let app;
  const secret = 'SYS_PRIVATE_PROMPT_SENTINEL', dev = 'DEV_PRIVATE_PROMPT_SENTINEL';
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ status: 'failed', model: 'fixture',
    detail: { value: { request: { messages: [{ role: 'system', content: secret }, { role: 'developer', content: dev },
      { role: 'user', content: JSON.stringify({ message: '分析本次合成数据', input: { prices: [10, 12], serialized: JSON.stringify({ systemPrompt: secret, userInput: '可见输入' }) } }) }] },
      response: { complete: false, text: JSON.stringify({ answer: '可见结果', debug: [{ role: 'developer', content: dev }], echo: secret }) } }, truncated: false },
    error: { code: 'MODEL_TIMEOUT', message: JSON.stringify({ message: '模型响应超时', systemPrompt: secret }), details: { rawRequest: secret } },
    platformDecision: { decision: 'reject', reason: secret },
  }) });
  try {
    app = renderer.createApp(ModelCallDetails, { runId: 'r', call: { id: 'c', traceUrl: '/desktop/api/sub-agent-runs/r/model-calls/c', traceAvailable: true,
      validationErrors: [{ message: '缺少交付字段', nested: JSON.stringify({ instructions: dev }) }] } }); app.mount(root);
    find(root, target => target.type === 'details').props.onToggle({ target: { open: true } }); await flush();
    assert.doesNotMatch(content(root), /SYS_PRIVATE|DEV_PRIVATE|systemPrompt|rawRequest/);
    assert.match(content(root), /分析本次合成数据|可见输入/); assert.doesNotMatch(content(root), /可见结果/);
    assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /MODEL_TIMEOUT|模型响应超时/); assert.doesNotMatch(content(root), /尚未完整返回/);
    assert.equal(find(root, target => target.type === 'a'), undefined);
  } finally { app?.unmount(); globalThis.fetch = originalFetch; }
});

test('model timeline summaries and errors cannot bypass the detail projection', async () => {
  const SubAgentPanel = await component('./components/SubAgentPanel.vue'), root = hostNode('root');
  const app = componentRenderer.createApp(SubAgentPanel, { controller: {}, run: { id: 'r', status: 'failed', kind: 'delegate', answer: '请求未完成',
    modelCalls: [{ id: 'c', status: 'failed', label: '模型处理', summary: '', errorSummary: JSON.stringify({ message: '模型超时', systemPrompt: 'timeline-private-prompt' }),
      error: JSON.stringify({ message: '模型超时', nested: { role: 'developer', content: 'timeline-private-developer' } }) }] } });
  try { app.mount(root); assert.match(componentContent(root), /当前网络连接失败/); assert.doesNotMatch(componentContent(root), /模型超时/); assert.doesNotMatch(componentContent(root), /timeline-private/); }
  finally { app.unmount(); }
});

test('truncated historical records show an explanation instead of raw head and tail', async () => {
  const originalFetch = globalThis.fetch, root = node('root'); let app;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ status: 'failed', detail: { truncated: true,
    value: { head: 'arbitrary-private-prefix', tail: 'arbitrary-private-suffix' } }, error: { code: 'MODEL_TIMEOUT', message: '模型超时' } }) });
  try {
    app = renderer.createApp(ModelCallDetails, { runId: 'r', call: { id: 'c', traceUrl: '/desktop/api/sub-agent-runs/r/model-calls/c', traceAvailable: true } }); app.mount(root);
    find(root, target => target.type === 'details').props.onToggle({ target: { open: true } }); await flush();
    assert.match(content(root), /原记录已截断/); assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /MODEL_TIMEOUT/);
    assert.doesNotMatch(content(root), /arbitrary-private/);
    assert.equal(find(root, target => target.props['aria-label'] === '本次业务输入' || target.props['aria-label'] === '模型输出节选'), undefined);
  } finally { app?.unmount(); globalThis.fetch = originalFetch; }
});
