import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { renderReportMarkdown } from './reportMarkdown.js';

const require = createRequire(import.meta.url);
const formatter = require('../workbench/pages/format.js');

test('valid multi-column report table keeps headings, timeline text and long values', () => {
  const url = 'https://example.com/very/long/research/path?company=超级长名称甲乙丙丁戊己庚辛壬癸&date=2024-09-27';
  const source = `## 事件脉络\n2024-09-27 公告发布。\n\n| 主体 | 时间 | 来源 |\n| :--- | ---: | :---: |\n| 超级长名称甲乙丙丁戊己庚辛壬癸股份有限公司 | 2024-09-27 | ${url} |\n| 第二主体 | 时间待核实 | 资料不足 |\n\n## 限制\n没有原文时注明限制。`;
  const html = renderReportMarkdown(source, formatter);
  assert.match(html, /<h4>事件脉络<\/h4>/);
  assert.match(html, /<table class="explore-report-table">/);
  assert.equal((html.match(/<th scope="col">/g) || []).length, 3);
  assert.equal((html.match(/<td>/g) || []).length, 6);
  assert.match(html, /2024-09-27 公告发布/);
  assert.match(html, /超级长名称甲乙丙丁戊己庚辛壬癸股份有限公司/);
  assert.match(html, /company=超级长名称甲乙丙丁戊己庚辛壬癸&amp;date=2024-09-27/);
  assert.match(html, /<h4>限制<\/h4>/);
  assert.match(html, /role="region" aria-label="报告表格，可横向滚动" tabindex="0"/);
});

test('escaped, quoted, code-span and link-destination pipes do not split cells', () => {
  const source = '| 主体 | 说明 |\n| --- | --- |\n| "甲|乙"公司 | `A|B` 与 甲\\|乙 |\n| [资料](https://example.com?q=x|y) | **加粗** |';
  const html = renderReportMarkdown(source, formatter);
  assert.equal((html.match(/<tr>/g) || []).length, 3);
  assert.equal((html.match(/<td>/g) || []).length, 4);
  assert.match(html, /&quot;甲\|乙&quot;公司/);
  assert.match(html, /<code>A\|B<\/code> 与 甲\|乙/);
  assert.match(html, /\[资料\]\(https:\/\/example\.com\?q=x\|y\)/);
  assert.match(html, /<strong>加粗<\/strong>/);
});

test('malformed separators or first-row column mismatch remain exactly on the old prose path', () => {
  for (const source of [
    '| A | B |\n| -- | --- |\n| x | y |',
    '| A | B |\n| --- | --- |\n| x | y | z |',
    '| A | B |\n| --- | --- |\n普通说明，不是表格行',
    '| A | B |\n| --- | --- |\n| `未闭合 | y |',
  ]) {
    assert.equal(renderReportMarkdown(source, formatter), formatter.renderProse(source));
  }
});

test('a malformed later row stops a table without dropping that original line', () => {
  const source = '| A | B |\n| --- | --- |\n| x | y |\n| 列数 | 不符 | 仍保留 |';
  const html = renderReportMarkdown(source, formatter);
  assert.equal((html.match(/<td>/g) || []).length, 2);
  assert.match(html, /列数 \| 不符 \| 仍保留/);
});

test('fenced code with table-looking lines remains on the existing prose path', () => {
  const fenced = '```text\n| A | B |\n| --- | --- |\n| x | y |\n```';
  assert.equal(renderReportMarkdown(fenced, formatter), formatter.renderProse(fenced));
  const mixed = `${fenced}\n\n| 真表 | 数据 |\n| --- | --- |\n| 甲 | 乙 |`;
  assert.equal((renderReportMarkdown(mixed, formatter).match(/<table class=/g) || []).length, 1);
});

test('table cells reuse the safe formatter and never inject HTML or unsafe links', () => {
  const source = '| 名称 | 说明 |\n| --- | --- |\n| <img src=x onerror=alert(1)> | [打开](javascript:alert(1)) **安全文本** |\n| <script>alert(2)</script> | `</td><svg onload=alert(3)>` |';
  const html = renderReportMarkdown(source, formatter);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /&lt;script&gt;alert\(2\)&lt;\/script&gt;/);
  assert.match(html, /<code>&lt;\/td&gt;&lt;svg onload=alert\(3\)&gt;<\/code>/);
  assert.match(html, /<strong>安全文本<\/strong>/);
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script'));
  assert.ok(!html.includes('<svg'));
  assert.ok(!html.includes('href="javascript:'));
});

test('non-table prose remains byte-for-byte the existing formatter output', () => {
  const prose = '## 结论\n\n- 已核实事实\n- 失败与限制\n\n长网址 https://example.com/a?x=1&y=2 和 `代码|内容`。';
  assert.equal(renderReportMarkdown(prose, formatter), formatter.renderProse(prose));
  assert.equal(renderReportMarkdown('<img onerror=alert(1)>', {}), '');
});
