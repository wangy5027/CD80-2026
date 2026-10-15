import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, reactive, nextTick } from 'vue';
import { useSubAgents } from './useSubAgents.js';
const response = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
const flush = async () => { await nextTick(); await new Promise(r => setImmediate(r)); };
function harness() {
  const s = reactive({ user: { id: 'a' }, currentTaskId: 'c', runtime: { subAgentAvailable: true }, streaming: false });
  let sub;
  const renderer = createRenderer({ createElement: () => ({}), createComment: () => ({}), insert() {}, remove() {}, parentNode() {}, nextSibling() {}, setText() {}, patchProp() {} });
  const app = renderer.createApp({ setup() { sub = useSubAgents(s, () => {}); return () => null; } }); app.mount({});
  return { s, sub, app };
}
test('sub-agent observation never mutates and ignores stale account response', async () => {
  const old = globalThis.fetch; let resolve; const calls = [];
  globalThis.fetch = (url, options = {}) => { calls.push(options.method || 'GET'); return new Promise(r => resolve = r); };
  const h = harness();
  try {
    const pending = h.sub.refresh(); h.s.user = { id: 'b' }; h.s.currentTaskId = null; await flush();
    resolve(response({ items: [{ id: 'private', status: 'running' }], activeRunId: 'private' })); await pending;
    assert.equal(h.sub.state.queue, null); assert.deepEqual(calls, ['GET']);
  } finally { h.app.unmount(); globalThis.fetch = old; }
});
test('late submitted task cannot overwrite a new account after detail fetch', async () => {
  const old = globalThis.fetch, storage = globalThis.localStorage;
  globalThis.localStorage = { setItem() {} };
  let detail;
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'POST') return response({ conversationId: 'c', id: 'r' });
    if (url.includes('/tasks/')) return new Promise(r => detail = r);
    return response({ items: [] });
  };
  const h = harness();
  try {
    const pending = h.sub.submit({ message: 'hello' }); await flush();
    h.s.user = { id: 'b' }; h.s.currentTaskId = null; h.s.currentTask = null; await flush();
    detail(response({ id: 'c', messages: [{ content: 'private' }] })); await pending;
    assert.equal(h.s.currentTask, null);
  } finally { h.app.unmount(); globalThis.fetch = old; globalThis.localStorage = storage; }
});

test('older poll cannot roll a finished task back to running', async () => {
 const old=globalThis.fetch; const pending=[];
 globalThis.fetch=async url => url.includes('/tasks/') ? response({messages:[]}) : new Promise(resolve=>pending.push(resolve));
 const h=harness();
 try {
  const first=h.sub.refresh(), second=h.sub.refresh();
  pending[1](response({revision:2,items:[{id:'r',status:'failed'}]})); await second;
  pending[0](response({revision:1,items:[{id:'r',status:'running'}]})); await first;
  assert.equal(h.sub.state.queue.items[0].status,'failed');
 } finally {h.app.unmount(); globalThis.fetch=old;}
});
