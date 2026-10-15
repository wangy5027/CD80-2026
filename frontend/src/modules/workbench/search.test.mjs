import test from 'node:test';
import assert from 'node:assert/strict';
import { readSse } from './api.js';
import { applyAssistantSearchEvent, mergeCompletedAssistantMessage, normalizeSearchMode, publicSearchSources, publicToolsUsed, safeSourceUrl } from './search.js';

test('skill and search SSE events update the pending reply and survive the done task', async () => {
  const wire = [
    'event: skill_loaded\ndata: {"name":"news-search"}\n\n',
    'event: search_status\ndata: {"status":"searching","query":"今日新闻"}\n\n',
    'event: search_sources\ndata: {"sources":[{"id":"1","title":"新闻标题","url":"https://example.com/news","publishedAt":"2026-10-12","snippet":"摘要"}]}\n\n',
    'event: search_status\ndata: {"status":"complete","query":"今日新闻"}\n\n',
    'event: done\ndata: {"task":{"messages":[{"role":"user","content":"今日新闻"},{"role":"assistant","content":"摘要回答","sources":[],"skillsUsed":[]}]}}\n\n',
  ].join('');
  const bytes = new TextEncoder().encode(wire);
  const chunks = [bytes.slice(0, 37), bytes.slice(37, 106), bytes.slice(106)];
  const stream = new ReadableStream({ start(controller) { chunks.forEach((part) => controller.enqueue(part)); controller.close(); } });
  const pending = { role: 'assistant', content: '' };
  let completed = null;
  const statuses = [];
  for await (const item of readSse(stream)) {
    if (item.event === 'done') completed = mergeCompletedAssistantMessage(item.data.task, pending);
    else statuses.push(applyAssistantSearchEvent(pending, item.event, item.data));
  }
  assert.match(statuses[0], /已加载技能/);
  assert.match(statuses[1], /正在联网搜索/);
  assert.match(statuses[2], /1 条来源/);
  assert.equal(statuses[3], '搜索完成，正在整理回复');
  assert.deepEqual(completed.messages.at(-1).skillsUsed, ['news-search']);
  assert.equal(completed.messages.at(-1).sources[0].url, 'https://example.com/news');
});

test('empty and error search outcomes are visible without exposing provider errors', () => {
  const pending = { role: 'assistant', content: '' };
  assert.equal(applyAssistantSearchEvent(pending, 'search_status', { status: 'empty' }), '未找到搜索结果，正在整理回复');
  const status = applyAssistantSearchEvent(pending, 'search_status', { status: 'error', message: 'private upstream detail' });
  assert.equal(status, '联网搜索失败，正在整理回复');
  assert.equal(status.includes('private upstream detail'), false);
});

test('source links reject executable and deceptive URLs before rendering', () => {
  assert.equal(safeSourceUrl('javascript:alert(1)'), '');
  assert.equal(safeSourceUrl('data:text/html,<script>'), '');
  assert.equal(safeSourceUrl('https://user:pass@example.com/'), '');
  assert.equal(safeSourceUrl('https://example.com/\nother'), '');
  const sources = publicSearchSources([
    { id: '1', title: '<img src=x onerror=alert(1)>', url: 'https://example.com/a' },
    { id: '2', title: 'duplicate', url: 'https://example.com/a' },
    { id: '3', title: 'unsafe', url: 'javascript:alert(1)' },
  ]);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].title, '<img src=x onerror=alert(1)>');
  assert.equal(sources[0].url, 'https://example.com/a');
  assert.equal(normalizeSearchMode('unexpected'), 'auto');
});

test('independent tool status and completed metadata work without a Skill', () => {
  const pending = { role: 'assistant', content: '' };
  assert.equal(applyAssistantSearchEvent(pending, 'tool_status', { status: 'running', name: 'sfc_news' }), '正在使用工具：sfc_news');
  assert.equal(applyAssistantSearchEvent(pending, 'tool_status', { status: 'complete', name: 'sfc_news' }), '工具 sfc_news 已完成，正在整理回复');
  assert.deepEqual(pending.toolsUsed, ['sfc_news']);
  assert.equal(applyAssistantSearchEvent(pending, 'tool_status', { status: 'error', name: 'sfc_quote', message: 'private upstream error' }).includes('private upstream error'), false);
  const task = mergeCompletedAssistantMessage({ messages: [{ role: 'user', content: '新闻' }, { role: 'assistant', content: '查询完毕', toolsUsed: [] }] }, pending);
  assert.deepEqual(task.messages.at(-1).toolsUsed, ['sfc_news']);
  assert.deepEqual(task.messages.at(-1).skillsUsed, []);
  assert.deepEqual(publicToolsUsed(['sfc_news', 'sfc_news', '<bad>']), ['sfc_news']);
});

test('source batches preserve earlier citations as more tools return', () => {
  const pending = { role: 'assistant', content: '' };
  applyAssistantSearchEvent(pending, 'search_sources', { sources: [{ id: 'S1', title: '甲', url: 'https://example.com/a' }] });
  applyAssistantSearchEvent(pending, 'search_sources', { sources: [{ id: 'S2', title: '乙', url: 'https://example.com/b' }] });
  assert.deepEqual(pending.sources.map(item => item.id), ['S1', 'S2']);
});
