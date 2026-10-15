import test from 'node:test';
import assert from 'node:assert/strict';
import { h, reactive } from 'vue';
import { component, content, find, flush, hostNode, renderer, submitEvent } from './assetComponentHarness.mjs';
import { useCreatedAppNavigation } from './useCreatedAppNavigation.js';

const AuthoringWorkspace = await component('./components/AuthoringWorkspace.vue');
const startedSession = (id = 'new-session', values = {}) => ({
  id, kind: 'app', intent: 'create', mode: 'prepare', phase: 'preparing', ...values,
});

function mountTracker() {
  const owner = reactive({ id: 'owner-a' });
  const authoring = { s: reactive({ owner: 'owner-a', busy: false, details: {}, items: [] }) };
  const saved = [];
  let trackStarted;
  const app = renderer.createApp({
    setup() {
      ({ trackStarted } = useCreatedAppNavigation(authoring, () => owner.id, value => saved.push(value)));
      return () => h('div');
    },
  });
  app.mount(hostNode('root'));
  return { app, owner, authoring, saved, trackStarted: (...args) => trackStarted(...args) };
}

test('only this started APP session navigates after its exact saved version appears', async () => {
  const t = mountTracker();
  try {
    const response = { session: startedSession() };
    t.authoring.s.items.push(startedSession('historical', { phase: 'completed', result: { assetId: 'old', versionId: 'v0' } }));
    t.trackStarted(response, 'owner-a');
    t.authoring.s.details['new-session'] = { session: startedSession('new-session', { phase: 'awaiting_user' }) };
    await flush();
    assert.deepEqual(t.saved, []);
    t.authoring.s.details['new-session'].session.phase = 'completed';
    t.authoring.s.details['new-session'].session.result = { assetId: 'app-new', versionId: 'v2' };
    await flush();
    assert.deepEqual(t.saved, [{ appId: 'app-new', versionId: 'v2' }]);
    t.trackStarted({ session: startedSession('new-session', { phase: 'completed', result: { assetId: 'app-new', versionId: 'v2' } }) }, 'owner-a');
    t.authoring.s.items.push(startedSession('new-session', { phase: 'completed', result: { assetId: 'app-new', versionId: 'v2' } }));
    await flush();
    assert.equal(t.saved.length, 1);
  } finally { t.app.unmount(); }
});

test('completed create waits for mutation to settle, while failures and drafts never navigate', async () => {
  const t = mountTracker();
  try {
    t.authoring.s.busy = true;
    t.trackStarted({ session: startedSession('immediate', { phase: 'completed', result: { assetId: 'app-ready', versionId: 'v1' } }) }, 'owner-a');
    assert.deepEqual(t.saved, []);
    t.authoring.s.busy = false;
    await flush();
    assert.deepEqual(t.saved, [{ appId: 'app-ready', versionId: 'v1' }]);

    for (const values of [
      { kind: 'skill' }, { mode: 'draft_only' }, { intent: 'upgrade' },
    ]) t.trackStarted({ session: startedSession(crypto.randomUUID(), values) }, 'owner-a');
    t.trackStarted({ session: startedSession('failed') }, 'owner-a');
    t.authoring.s.items.push(startedSession('failed', { phase: 'failed', result: { assetId: 'not-saved', versionId: 'v1' } }));
    await flush();
    assert.equal(t.saved.length, 1);
  } finally { t.app.unmount(); }
});

test('unknown-result retry can track the recovered session once', async () => {
  const t = mountTracker();
  try {
    const response = { session: startedSession('recovered') };
    t.trackStarted(response, 'owner-a');
    t.trackStarted(response, 'owner-a');
    t.authoring.s.items.push(startedSession('recovered', { phase: 'completed', result: { assetId: 'app-recovered', versionId: 'v3' } }));
    await flush();
    assert.deepEqual(t.saved, [{ appId: 'app-recovered', versionId: 'v3' }]);
  } finally { t.app.unmount(); }
});

test('switching accounts or unmounting stops a late completion from navigating', async () => {
  const t = mountTracker();
  t.trackStarted({ session: startedSession('old-owner') }, 'owner-a');
  t.owner.id = 'owner-b'; t.authoring.s.owner = 'owner-b';
  t.authoring.s.details['old-owner'] = { session: startedSession('old-owner', { phase: 'completed', result: { assetId: 'private-a', versionId: 'v1' } }) };
  t.trackStarted({ session: startedSession('late-old', { phase: 'completed', result: { assetId: 'private-a', versionId: 'v1' } }) }, 'owner-a');
  await flush();
  assert.deepEqual(t.saved, []);
  t.trackStarted({ session: startedSession('new-owner') }, 'owner-b');
  t.app.unmount();
  t.authoring.s.details['new-owner'] = { session: startedSession('new-owner', { phase: 'completed', result: { assetId: 'private-b', versionId: 'v1' } }) };
  await flush();
  assert.deepEqual(t.saved, []);
});

test('workspace emits the exact accepted create response and its owner snapshot before closing', async () => {
  const response = { session: startedSession('created') };
  const events = [], controller = { s: reactive({ busy: false, error: '', pendingRetry: false }), create: async () => response, close() {} };
  const root = hostNode('root');
  const app = renderer.createApp(AuthoringWorkspace, { ownerId: 'owner-a', controller,
    context: { kind: 'app', intent: 'create', instruction: '按要求创建 APP' },
    onStarted: (...args) => events.push(['started', ...args]), onClose: () => events.push(['close']) });
  try {
    app.mount(root); await flush();
    await find(root, node => node.tag === 'form').props.onSubmit(submitEvent());
    assert.deepEqual(events, [['started', response, 'owner-a'], ['close']]);
  } finally { app.unmount(); }
});

test('workspace recovery emits started only when the same create submission is recovered', async () => {
  const response = { session: startedSession('recovered') };
  const events = [], state = reactive({ busy: false, error: '', pendingRetry: false });
  const controller = { s: state, create: async () => { state.pendingRetry = true; return null; }, retry: async () => response, close() {} };
  const root = hostNode('root');
  const app = renderer.createApp(AuthoringWorkspace, { ownerId: 'owner-a', controller,
    context: { kind: 'app', intent: 'create', instruction: '按要求创建 APP' },
    onStarted: (...args) => events.push(['started', ...args]), onClose: () => events.push(['close']) });
  try {
    app.mount(root); await flush();
    await find(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await flush();
    assert.deepEqual(events, []);
    const retry = find(root, node => node.tag === 'button' && content(node) === '核实并恢复同一次提交');
    await retry.props.onClick(); await flush();
    assert.deepEqual(events, [['started', response, 'owner-a'], ['close']]);
  } finally { app.unmount(); }
});
