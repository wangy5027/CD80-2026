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
