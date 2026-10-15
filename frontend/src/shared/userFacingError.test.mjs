import test from 'node:test';
import assert from 'node:assert/strict';
import { USER_FACING_ERROR, userFacingMessageContent } from './userFacingError.js';
import { request, readSse } from '../modules/workbench/api.js';
import { applyAssistantSearchEvent } from '../modules/workbench/search.js';

test('transport diagnostics remain available without becoming failed-message copy', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ error: 'private app.json path error', code: 'REVISION_CONFLICT', details: { revision: 7 } }), { status: 409, headers: { 'content-type': 'application/json' } });
  try {
    await assert.rejects(request('/fixture'), error => {
      assert.equal(error.status, 409);
      assert.equal(error.code, 'REVISION_CONFLICT');
      assert.equal(error.details.revision, 7);
      assert.equal(error.message, 'private app.json path error');
      assert.equal(userFacingMessageContent({ role: 'assistant', error, content: error.message }), USER_FACING_ERROR);
      return true;
    });
  } finally { globalThis.fetch = original; }
});

test('stream error metadata is retained while failed chat and search presentation is generic', async () => {
  const data = new TextEncoder().encode('event: error\ndata: {"message":"private upstream trace","code":"MODEL_TIMEOUT"}\n\nevent: done\ndata: {}\n\n');
  const stream = new ReadableStream({ start(controller) { controller.enqueue(data); controller.close(); } });
  const events = []; for await (const event of readSse(stream)) events.push(event);
  assert.equal(events[0].data.code, 'MODEL_TIMEOUT');
  assert.equal(userFacingMessageContent({ role: 'assistant', status: 'failed', content: events[0].data.message }), USER_FACING_ERROR);
  assert.equal(applyAssistantSearchEvent({ role: 'assistant' }, 'tool_status', { name: 'example_tool', status: 'failed', message: 'private tool trace' }), USER_FACING_ERROR);
  assert.equal(userFacingMessageContent({ role: 'assistant', content: '正常结果：42', status: 'succeeded' }), '正常结果：42');
  assert.equal(userFacingMessageContent({ role: 'user', error: 'quoted', content: '请解释这个错误：app.json' }), '请解释这个错误：app.json');
});
