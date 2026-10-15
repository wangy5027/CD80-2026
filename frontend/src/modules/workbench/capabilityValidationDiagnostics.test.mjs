import test from 'node:test';
import assert from 'node:assert/strict';
import { component, renderer, hostNode, find, content, flush } from './assetComponentHarness.mjs';

const Panel = await component('./components/CapabilityLibraryPanel.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const settle = async () => { for (let n = 0; n < 5; n++) await flush(); };

test('legacy failed Skill validation retrieves actual evidence and shows a bounded summary', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  const calls = [], asset = { assetId: 'a1', kind: 'skill', name: '历史查询', description: '', latestVersionId: 'v1', lifecycle: 'active', readiness: 'unverified', enabled: false };
  const version = { versionId: 'v1', configurationId: 'c1', contentDigest: 'test-digest', readiness: 'unverified', issues: [], usageExamples: [{ title: '小区间', prompt: '查询日线', criteria: '依据真实结果' }],
    manifest: { kind: 'skill', files: [], dependencies: [], permissions: { inputFiles: false }, skill: { entry: 'SKILL.md' } } };
  const evidence = { verificationId: 'check-failed', status: 'failed', error: { error: '模型同轮重试未恢复' }, result: { traceback: 'PRIVATE_VALIDATION_TRACE' },
    executionEvidence: [{ type: 'tool_completed', invocationId: 'real-1' }],
    modelRequests: [{ stage: 'generating', status: 'failed', outputChars: 8, partialOutputAccepted: false, partialOutput: 'PARTIAL-NOT-RESULT' }, { stage: 'awaiting_headers', status: 'failed' }] };
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, method: options.method || 'GET' });
    if (path.endsWith('/verifications')) return json({ jobId: 'job-failed', verificationId: 'check-failed', status: 'failed', error: { error: '模型同轮重试未恢复' } });
    if (path.endsWith('/verifications/check-failed')) return json(evidence);
    if (path.includes('/versions?')) return json({ items: [version] });
    if (path.endsWith('/assets/a1')) return json(asset);
    return json({ items: path.includes('/assets?') ? [asset] : [] });
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'audit', initialAssetId: 'a1' });
  try {
    app.mount(root); await settle();
    find(root, n => n.tag === 'button' && content(n) === '试试这个示例').props.onClick(); await settle();
    find(root, n => n.tag === 'button' && content(n) === '开始验证').props.onClick(); await settle();
    assert.equal(calls.filter(c => c.path.endsWith('/verifications')).length, 1);
    assert.ok(calls.some(c => c.path.endsWith('/verifications/check-failed')));
    const view = content(find(root, n => n.props['aria-label'] === '验证执行情况'));
    assert.match(view, /已完成 1 次工具调用/); assert.match(view, /模型请求 2 次/); assert.match(view, /当前网络连接失败/); assert.doesNotMatch(view, /等待模型响应|模型同轮重试未恢复/);
    assert.match(view, /部分回答未作为验证结果/); assert.doesNotMatch(content(root), /PARTIAL-NOT-RESULT|PRIVATE_VALIDATION_TRACE/);
    assert.equal(find(root, n => n.tag === 'button' && content(n) === '确认符合预期'), undefined);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});
