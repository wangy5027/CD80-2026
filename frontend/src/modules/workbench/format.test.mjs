import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const format = require('./pages/format.js');

test('assistant message markup escapes untrusted HTML and does not create links', () => {
  const html = format.renderAssistant('提醒：<img src=x onerror=alert(1)> https://example.invalid', 0);
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<a '));
});

test('fenced code remains escaped and copy extraction returns the original text', () => {
  const message = '说明\n\n' + String.fromCharCode(96).repeat(3) + 'html\n<button>测试</button>\n' + String.fromCharCode(96).repeat(3);
  const html = format.renderAssistant(message, 2);
  assert.ok(html.includes('data-action="copy-code"'));
  assert.ok(html.includes('&lt;button&gt;'));
  assert.equal(format.codeBlockAt(message, 0), '<button>测试</button>');
});

const source = { id: 'S1', title: '学校新闻', url: 'https://example.com/news?id=1&lang=zh' };

test('known source IDs and old Markdown links become compact citations without losing descriptive text', () => {
  const html = format.renderAssistant(`今天的新闻。[S1]\n\n来源：[链接](${source.url})\n\n[学校举办活动](${source.url})`, 0, [source]);
  assert.equal((html.match(/class="message-citation"/g) || []).length, 3);
  assert.ok(html.includes('学校举办活动'));
  assert.ok(!html.includes('[链接]('));
  assert.ok(html.includes('data-source-url="https://example.com/news?id=1&amp;lang=zh"'));
  assert.ok(html.includes('aria-label="查看来源 1：学校新闻"'));
});

test('citations only activate real returned sources, never arbitrary URLs or executable markup', () => {
  const unsafe = { id: 'S2', url: 'javascript:alert(1)', title: 'bad' };
  const html = format.renderAssistant('[S1] [S2] [S99] [链接](https://other.example/news) <img src=x onerror=alert(1)>', 0, [{ ...source, title: '\"><img src=x onerror=alert(1)>' }, unsafe]);
  assert.equal((html.match(/class="message-citation"/g) || []).length, 1);
  assert.ok(html.includes('[S2] [S99]'));
  assert.ok(html.includes('[链接](https://other.example/news)'));
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<a '));
  assert.ok(html.includes('&quot;&gt;&lt;img'));
});

test('citation-like text in inline and fenced code stays literal and copyable', () => {
  const block = `[S1]\n[链接](${source.url})`;
  const text = '`[S1]`\n\n```markdown\n' + block + '\n```';
  const html = format.renderAssistant(text, 2, [source]);
  assert.ok(!html.includes('class="message-citation"'));
  assert.ok(html.includes('<code>[S1]</code>'));
  assert.equal(format.codeBlockAt(text, 0), block);
});

test('known bare URLs, URL fragments, and balanced parentheses work without matching unrelated suffixes', () => {
  const nested = { id: 'S2', title: '带括号', url: 'https://example.com/topic_(news)' };
  const html = format.renderAssistant(`来源：${source.url}。\n[链接](${source.url}#section)\n[链接](${nested.url})\n来源：${nested.url}\n${source.url}-different`, 0, [source, nested]);
  assert.equal((html.match(/class="message-citation"/g) || []).length, 4);
  assert.ok(html.includes('-different'));
});

test('ordinary brackets and source IDs before Markdown links retain their own meaning', () => {
  const html = format.renderAssistant(`[说明] 参见 [链接](${source.url})，另一处 [S1] 与 [链接](${source.url})`, 0, [source]);
  assert.ok(html.includes('[说明] 参见 '));
  assert.equal((html.match(/class="message-citation"/g) || []).length, 3);
  assert.ok(!html.includes('[链接]'));
});
