import test from 'node:test';
import assert from 'node:assert/strict';
import { readSse, request } from './api.js';

test('chat SSE preserves split UTF-8, CRLF blocks, and the final event', async () => {
  const encoded = new TextEncoder().encode('event: meta\r\ndata: {"taskId":"t-1"}\r\n\r\nevent: token\r\ndata: {"text":"成都"}\r\n\r\nevent: done\ndata: {"task":{"id":"t-1"}}');
  const chunks = [];
  for (let i = 0; i < encoded.length; i += 2) chunks.push(encoded.slice(i, i + 2));
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
  const events = [];
  for await (const event of readSse(stream)) events.push(event);
  assert.deepEqual(events.map((event) => event.event), ['meta', 'token', 'done']);
  assert.equal(events[1].data.text, '成都');
  assert.equal(events[2].data.task.id, 't-1');
});

test('request keeps the existing JSON API path and shows backend errors', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/desktop/api/projects');
      assert.equal(options.credentials, 'include');
      assert.equal(options.headers['Content-Type'], 'application/json');
      assert.deepEqual(JSON.parse(options.body), { name: '项目' });
      return new Response(JSON.stringify({ error: '名称不能重复' }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      });
    };
    await assert.rejects(request('/projects', { method: 'POST', body: { name: '项目' } }), /名称不能重复/);
  } finally {
    globalThis.fetch = original;
  }
});
