import test from 'node:test';
import assert from 'node:assert/strict';
import { reactive } from 'vue';
import { component, renderer, hostNode, find, all, content, flush, submitEvent } from './assetComponentHarness.mjs';
const AuthoringPanel = await component('./components/AuthoringPanel.vue');
const AuthoringWorkspace = await component('./components/AuthoringWorkspace.vue');
const AuthoringShelf = await component('./components/AuthoringShelf.vue');
const AssetAuthoringPanel = await component('./components/AssetAuthoringPanel.vue');
const CapabilityLibraryPanel = await component('./components/CapabilityLibraryPanel.vue');
const SubAgentPanel = await component('./components/SubAgentPanel.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const settle = async () => { for (let i = 0; i < 4; i++) await flush(); };
const button = (root, label) => find(root, node => node.tag === 'button' && content(node) === label);
const makeController = (detail = {}) => {
  const calls = [], s = reactive({ selectedId: 'session', open: true, details: { session: {
    session: { id: 'session', kind: 'tool', revision: 3, phase: 'awaiting_review', currentCandidateId: 'candidate', allowedActions: ['messages','draft_only','save_and_enable'], instruction: '整理规则' },
    candidate: { id: 'candidate', contentDigest: 'digest', content: { name: '方法', files: [{ path: 'tool.py', text: '<script>never execute</script>' }] } },
    reviewSnapshot: { id: 'review', candidateId: 'candidate', contentDigest: 'digest', reviewDigest: 'review-digest' },
    budget: { id: 'round', consumed: { activeMilliseconds: 1000, modelRequests: 8, repairRounds: 1 } }, evidence: [], ...detail,
  } }, items: [], busy: false, error: '', errorCode: '', pendingRetry: false });
  return { s, calls, confirm: async (...args) => { calls.push(['confirm',...args]); return {}; }, append: async (...args) => { calls.push(['append',...args]); return {}; }, resume: (...args) => calls.push(['resume',...args]), cancel: (...args) => calls.push(['cancel',...args]), close: () => { calls.push(['close']); s.open = false; }, create: async body => { calls.push(['create',body]); return { session: { id: 'created' } }; }, read: (...args) => calls.push(['read',...args]), editContent: async (...args) => { calls.push(['edit',...args]); return {}; } };
};

test('shared review panel shows only current actual evidence, literal source, and no implicit execution', async () => {
  const controller = makeController({ evidence: [{ candidateId: 'old', executionMode: 'real', caseResults: [{ result: 'OLD RESULT', status: 'passed' }] }, { candidateId: 'candidate', executionMode: 'simulated', caseResults: [{ label: '不同输入', result: 'NEW RESULT', status: 'passed' }] }] });
  const root = hostNode('root'), app = renderer.createApp(AuthoringPanel, { controller, ownerId: 'owner' });
  try {
    app.mount(root); await flush(); assert.equal(controller.calls.length, 0);
    assert.match(content(root), /NEW RESULT/); assert.doesNotMatch(content(root), /OLD RESULT/); assert.match(content(root), /模拟检查/); assert.match(content(root), /8 \/ 32/);
    assert.match(content(root), /<script>never execute/); assert.equal(find(root, node => node.tag === 'script' || node.props.innerHTML), undefined);
    await button(root,'确认保存并使用').props.onClick(); assert.deepEqual(controller.calls[0], ['confirm','session','save_and_enable']);
    assert.equal(find(root, node => node.props['aria-modal'] === 'true').props.role, 'dialog');
  } finally { app.unmount(); }
});

test('chat edit pending receipt keeps the old review visible but disables confirmation and identifies its revision', async () => {
  const controller = makeController(); controller.s.pendingChat = { session: { revision: 3, runId: 'queued' } };
  const root = hostNode('root'), app = renderer.createApp(AuthoringPanel, { controller, ownerId: 'chat-pending-owner' });
  try {
    app.mount(root); await flush(); assert.match(content(root), /聊天中的修改正在同步/);
    assert.equal(button(root,'确认保存并使用').props.disabled, true); assert.equal(button(root,'只存草稿').props.disabled, true);
    const panel = find(root, node => node.props.role === 'dialog');
    assert.equal(panel.props['data-authoring-revision'], 3); assert.equal(panel.props['data-authoring-candidate-id'], 'candidate');
    delete controller.s.pendingChat.session; await flush(); assert.equal(button(root,'确认保存并使用').props.disabled, false);
  } finally { app.unmount(); }
});
test('draft with no candidate still supports only save draft and does not claim validation', async () => {
  const controller = makeController({ session: { id: 'session', kind: 'app', revision: 1, phase: 'failed', allowedActions: ['draft_only','messages'] }, candidate: null, reviewSnapshot: null });
  const root = hostNode('root'), app = renderer.createApp(AuthoringPanel, { controller, ownerId: 'owner' });
  try { app.mount(root); await flush(); assert.equal(button(root,'确认保存并使用'), undefined); await button(root,'只存草稿').props.onClick(); assert.deepEqual(controller.calls[0], ['confirm','session','draft_only']); assert.equal(find(root, node => node.props['aria-label'] === '实际验证结果'), undefined); }
  finally { app.unmount(); }
});
test('completed APP creation exposes exact saved version without offering old save-and-enable', async () => {
  const controller = makeController({ session: { id: 'session', kind: 'app', phase: 'completed', mode: 'prepare', result: { assetId: 'app-new', versionId: 'av-2' }, allowedActions: ['save_and_enable'] }, commit: { mode: 'save_only', stage: 'completed', targetAssetId: 'app-new', targetVersionId: 'av-2' } });
  const events = [], root = hostNode('root'), app = renderer.createApp(AuthoringPanel, { controller, ownerId: 'owner', onAsset: value => events.push(value) });
  try {
    app.mount(root); await flush();
    assert.match(content(root), /已保存到我的 APP · 未验证/);
    assert.equal(button(root, '确认保存并使用'), undefined);
    button(root, '打开应用详情').props.onClick();
    assert.deepEqual(events, [{ kind: 'app', assetId: 'app-new', versionId: 'av-2' }]);
    assert.equal(controller.calls.some(item => item[0] === 'confirm'), false);
  } finally { app.unmount(); }
});
test('completed APP session in the shelf renders its detail and opens through the passed authoring controller', async () => {
  const oldFetch = globalThis.fetch, controller = makeController(), root = hostNode('root');
  const session = { id: 'completed-app', kind: 'app', phase: 'completed', mode: 'prepare', result: { assetId: 'app-new', versionId: 'av-2' } };
  controller.s.items = [session];
  controller.s.details[session.id] = { session, candidate: { content: { name: '已保存的个人 APP' } } };
  globalThis.fetch = async () => json({ items: [] });
  const app = renderer.createApp(AuthoringShelf, { controller, ownerId: 'owner', kinds: ['app'] });
  try {
    app.mount(root); await settle();
    assert.match(content(root), /已保存的个人 APP/);
    assert.match(content(root), /尚未验证或启用/);
    find(root, node => node.tag === 'button' && content(node).startsWith('查看并继续')).props.onClick();
    assert.deepEqual(controller.calls.at(-1), ['read', 'completed-app']);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});
test('continuous editing keeps unsent input after conflict and does not start another session', async () => {
  const controller = makeController(); controller.append = async () => { controller.s.error = '其他页面已修改'; controller.s.errorCode = 'REVISION_CONFLICT'; return null; };
  const root = hostNode('root'), app = renderer.createApp(AuthoringPanel, { controller, ownerId: 'conflict-owner' });
  try {
    app.mount(root); await flush();
    const form = find(root, node => node.props.class === 'author-revise'), textarea = find(form, node => node.tag === 'textarea');
    textarea.props['onUpdate:modelValue']('保留原口径，仅调整输出'); await flush(); await form.props.onSubmit(submitEvent()); await flush();
    assert.equal(textarea.value, '保留原口径，仅调整输出'); assert.match(content(root), /当前网络连接失败/); assert.equal(controller.calls.length, 0);
  } finally { app.unmount(); }
});
test('budget continuation is explicit and disabled when the signed confirmation is absent', async () => {
  const controller = makeController({ session: { id: 'session', kind: 'skill', revision: 7, phase: 'budget_paused', allowedActions: ['resume','extend_budget','draft_only'] }, budget: { id: 'round', consumed: { modelRequests: 32, repairRounds: 2, activeMilliseconds: 600000 } } });
  const root = hostNode('root'), app = renderer.createApp(AuthoringPanel, { controller, ownerId: 'budget-owner' });
  try {
    app.mount(root); await flush(); const extend = () => button(root,'确认再处理一轮（10分钟 / 32次请求 / 2轮修复）');
    assert.equal(extend().props.disabled, true); assert.equal(controller.calls.length, 0);
    controller.s.details.session.budget.confirmationId = 'signed'; await flush(); assert.equal(extend().props.disabled, false);
    extend().props.onClick(); assert.deepEqual(controller.calls[0], ['resume','session','extend_budget']);
  } finally { app.unmount(); }
});
for (const kind of ['app','tool','skill']) test(`${kind} creation and discussion capture use the same create contract without manual technical fields`, async () => {
  for (const intent of ['create','capture']) {
    const controller = makeController(), root = hostNode('root');
    const sourceRefs = intent === 'capture' ? [{ kind: 'conversation', reference: { conversationId: 'conversation' } }] : [];
    const app = renderer.createApp(AuthoringWorkspace, { controller, ownerId: kind + intent, context: { kind, intent, instruction: '保留规则整理为可复用方法', sourceRefs } });
    try {
      app.mount(root); await flush(); assert.equal(controller.calls.length, 0);
      await find(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await flush();
      assert.deepEqual(controller.calls[0], ['create', { kind, intent, instruction: '保留规则整理为可复用方法', mode: 'prepare', sourceRefs }]);
      assert.doesNotMatch(content(root), /选择成功步骤|填写测试用例/);
    } finally { app.unmount(); }
  }
});
test('skill-pool APP creation fixes the asset kind and only starts complete save-first authoring', async () => {
  const controller = makeController(), root = hostNode('root');
  const app = renderer.createApp(AuthoringWorkspace, { controller, ownerId: 'pool-owner', context: { kind: 'app', intent: 'create', hideDraftOnly: true } });
  try {
    app.mount(root); await flush();
    assert.equal(button(root, '只存草稿'), undefined);
    assert.equal(find(root, node => node.tag === 'select').props.disabled, true);
    const textarea = find(root, node => node.tag === 'textarea');
    textarea.props['onUpdate:modelValue']('接收两组数，输出合计与来源说明'); await flush();
    await find(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await flush();
    assert.deepEqual(controller.calls[0], ['create', { kind: 'app', intent: 'create', instruction: '接收两组数，输出合计与来源说明', mode: 'prepare', sourceRefs: [] }]);
  } finally { app.unmount(); }
});
test('portable import preserves unknown original with no authoring request until prepare is explicit', async () => {
  const oldFetch = globalThis.fetch, calls = [], controller = makeController(), root = hostNode('root');
  globalThis.fetch = async (path, options) => { calls.push({ path, options }); return json({ id: 'portable', kind: 'tool', name: 'unknown.bin', runtimeReadiness: 'unprepared', exportUrl: '/desktop/api/portable-assets/portable/export' }); };
  const app = renderer.createApp(AuthoringWorkspace, { controller, ownerId: 'import-owner', context: { kind: 'tool', action: 'import' } });
  try {
    app.mount(root); await flush(); find(root, node => node.tag === 'input' && node.props.type === 'file').props.onChange({ target: { files: [new File(['opaque original'], 'unknown.bin')] } }); await flush();
    await find(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await settle();
    assert.equal(calls.length, 1); assert.equal(calls[0].path, '/desktop/api/portable-assets/imports'); assert.equal(calls[0].options.body.get('kind'), 'tool'); assert.ok(calls[0].options.headers['Idempotency-Key']);
    assert.equal(controller.calls.length, 0); assert.match(content(root), /原件已保存 · 待准备/); assert.equal(find(root, node => node.tag === 'a').props.href, '/desktop/api/portable-assets/portable/export');
    await find(root, node => node.tag === 'form').props.onSubmit(submitEvent());
    assert.equal(controller.calls[0][1].intent, 'prepare_import'); assert.deepEqual(controller.calls[0][1].sourceRefs, [{ kind: 'import_draft', reference: { portableAssetId: 'portable' } }]);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});
test('the three library new and import entry points delegate without calling legacy mutation routes', async () => {
  const oldFetch = globalThis.fetch, calls = [];
  globalThis.fetch = async (path, options = {}) => { calls.push({ path, method: options.method || 'GET' }); return json({ items: [] }); };
  try {
    for (const kind of ['app','skill','tool']) {
      const events = [], root = hostNode('root'), app = renderer.createApp(kind === 'app' ? AssetAuthoringPanel : CapabilityLibraryPanel, { ownerId: 'entry-owner', unifiedAuthoring: true, onAuthor: value => events.push(value) });
      try {
        app.mount(root); await settle(); if (kind === 'tool') { find(root, node => node.props.role === 'tab' && content(node).startsWith('Tools')).props.onClick(); await flush(); }
        await button(root, kind === 'app' ? '新建应用' : '新建草稿').props.onClick(); await flush();
        await button(root, kind === 'app' ? '导入应用原件' : '导入能力').props.onClick();
        assert.deepEqual(events, [{ kind, intent: 'create' }, { kind, action: 'import' }]);
      } finally { app.unmount(); }
    }
    assert.ok(calls.every(call => call.method === 'GET'));
  } finally { globalThis.fetch = oldFetch; }
});
test('failed tasks with no completed code may be captured without claiming historical success', async () => {
  const events = [], run = { id: 'run', kind: 'delegate', status: 'failed', steps: [], error: '没有完成执行' }, root = hostNode('root');
  const app = renderer.createApp(SubAgentPanel, { controller: {}, run, unifiedAuthoring: true, onCapture: (...args) => events.push(args) });
  try { app.mount(root); await flush(); assert.match(content(root), /推断或新生成/); button(root,'沉淀为 Skill').props.onClick(); assert.deepEqual(events[0], ['skill',run]); }
  finally { app.unmount(); }
});
for (const kind of ['app','tool','skill']) test(`${kind} edit and import preparation preserve exact source and base target`, async () => {
  for (const intent of ['edit','prepare_import']) {
    const controller = makeController(), root = hostNode('root'), target = { assetId: 'owned-asset', baseVersionId: 'immutable-v1', baseRevision: 9 };
    const sourceRefs = intent === 'prepare_import' ? [{ kind: 'import_draft', reference: { portableAssetId: 'stored-original' } }] : [];
    const app = renderer.createApp(AuthoringWorkspace, { controller, ownerId: kind + intent, context: { kind, intent, target, sourceRefs, instruction: '保持旧版本不变，准备新的候选。' } });
    try {
      app.mount(root); await flush(); await find(root, node => node.tag === 'form').props.onSubmit(submitEvent());
      assert.deepEqual(controller.calls[0], ['create',{ kind, intent, target, sourceRefs, instruction: '保持旧版本不变，准备新的候选。', mode: 'prepare' }]);
      assert.equal(find(root, node => node.tag === 'select').props.disabled, true);
    } finally { app.unmount(); }
  }
});
test('draft-only advanced editor restores raw draftContent instead of an older candidate', async () => {
  const controller = makeController(); controller.s.details.session.session.draftContent = { name: '尚未检查的新草稿', files: [] };
  const root = hostNode('root'), app = renderer.createApp(AuthoringPanel, { controller, ownerId: 'raw-owner' });
  try { app.mount(root); await flush(); const editor = find(root, node => node.props['aria-label'] === '创作候选完整内容'); assert.match(editor.value, /尚未检查的新草稿/); assert.doesNotMatch(editor.value, /never execute/); }
  finally { app.unmount(); }
});
test('unknown model replies require explicit per-request abandonment before continuation', async () => {
  const controller = makeController({ session: { id: 'session', kind: 'tool', revision: 7, phase: 'interrupted', allowedActions: ['resume'] }, budget: { id: 'round', unknownRequests: [{ id: 'sent-1', model: 'test model', sentAt: '2026-10-14' }], consumed: { modelRequests: 1 } } });
  const root = hostNode('root'), app = renderer.createApp(AuthoringPanel, { controller, ownerId: 'unknown-owner' });
  try {
    app.mount(root); await flush(); assert.equal(button(root,'按剩余额度继续').props.disabled, true); assert.equal(controller.calls.length, 0);
    assert.match(content(root), /仍计入额度/); assert.match(content(root), /不会重放旧请求/);
    find(root, node => node.tag === 'input' && node.props.type === 'checkbox').props['onUpdate:modelValue'](['sent-1']); await flush();
    assert.equal(button(root,'按剩余额度继续').props.disabled, false); button(root,'按剩余额度继续').props.onClick();
    assert.deepEqual(controller.calls[0], ['resume','session','resume',['sent-1']]);
  } finally { app.unmount(); }
});
test('adding files to a live draft submits only explicitly selected owned identities on the same session', async () => {
  const controller = makeController(), root = hostNode('root'), app = renderer.createApp(AuthoringPanel, { controller, ownerId: 'file-owner', files: [{ id:'file-one',name:'新资料' }, { id:'file-two',name:'未授权' }] });
  try {
    app.mount(root); await flush();
    const choices = all(root, node => node.tag === 'input' && node.props.type === 'checkbox'); assert.equal(choices.length, 2);
    assert.equal(controller.calls.length, 0); choices[0].props['onUpdate:modelValue'](['file-one']); await flush();
    await find(root, node => node.props.class === 'author-revise').props.onSubmit(submitEvent());
    assert.deepEqual(controller.calls[0], ['append','session','已补充本次授权文件，请沿用原需求继续整理与验证。',[{kind:'upload',reference:{fileId:'file-one'}}]]);
  } finally { app.unmount(); }
});
test('missing connection is resolved by an owned selector and same-draft answer without JSON or automatic choice', async () => {
  const oldFetch=globalThis.fetch,controller=makeController(),root=hostNode('root');
  controller.s.details.session.session.question={id:'connection-question',kind:'authorization',prompt:'请选择报价服务连接'};
  controller.s.details.session.session.allowedActions=['answers','messages','draft_only'];
  controller.s.details.session.runtimeBindings={calendar:'calendar-owned'};
  controller.s.details.session.candidate.content={manifest:{name:'报价',tool:{execution:{type:'mcp_http',connectionIdentity:'quotes'}}},files:[]};
  controller.answer=async(...args)=>{controller.calls.push(['answer',...args]);return {};};
  globalThis.fetch=async()=>json({items:[{connectionId:'mine',sourceIdentity:'quotes',name:'本人已配置报价',configured:true,enabled:true},{connectionId:'off',sourceIdentity:'quotes',name:'已停用连接',configured:true,enabled:false}]});
  const app=renderer.createApp(AuthoringPanel,{controller,ownerId:'connection-form-owner'});
  try {
    app.mount(root);await settle();const select=find(root,node=>node.props['aria-label']==='连接 · quotes');assert.ok(select);
    assert.equal(select.value,'');assert.equal(controller.calls.length,0);assert.equal(find(select,node=>node.tag==='option'&&node.props.value==='off').props.disabled,true);
    select.props.onChange({target:{value:'mine'}});await flush();
    assert.equal(button(root,'提交回答').props.disabled,false);await find(root,node=>node.props.class==='author-question').children.find(node=>node.tag==='form').props.onSubmit(submitEvent());
    assert.deepEqual(controller.calls[0],['answer','session','已选择本人的连接，请按当前需求继续准备并重新检查权限。',[],{calendar:'calendar-owned',quotes:'mine'}]);
  } finally {app.unmount();globalThis.fetch=oldFetch;}
});
