import test from 'node:test';
import assert from 'node:assert/strict';
import { sourceBodyText, sourceDisplayText } from './sourceText.js';

test('saved HTML tables become readable source text without empty placeholder rows', () => {
  const saved = '<table class="news"><tr><th>指标</th><th>数值</th></tr>' +
    '<tr><td>营业收入</td><td>12.5 亿元</td></tr>' +
    '<tr><td>增长率</td><td>0%</td><td>-12.3%</td></tr>' +
    '<tr><td>--</td><td>--</td></tr>' +
    '<tr><td>&nbsp;</td><td>　</td></tr><tr><td></td><td></td></tr>' +
    '<tr><td>同比</td><td>增长 &lt; 5%</td></tr></table>';
  assert.equal(sourceDisplayText(saved), '指标 | 数值\n营业收入 | 12.5 亿元\n增长率 | 0% | -12.3%\n同比 | 增长 < 5%');
});

test('escaped and double-escaped saved HTML display the same source data', () => {
  const html = '<table><tr><td>来源</td><td>公开报道</td></tr></table>';
  const escaped = '&lt;table&gt;&lt;tr&gt;&lt;td&gt;来源&lt;/td&gt;&lt;td&gt;公开报道&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;';
  const doubleEscaped = escaped.replaceAll('&', '&amp;');
  assert.equal(sourceDisplayText(html), '来源 | 公开报道');
  assert.equal(sourceDisplayText(escaped), '来源 | 公开报道');
  assert.equal(sourceDisplayText(doubleEscaped), '来源 | 公开报道');
});

test('script, style, comments and tag attributes never appear in readable source text', () => {
  const saved = '<!DOCTYPE html><!-- internal --><script>alert("unsafe")</script>' +
    '<style>.secret{display:none}</style><p onclick="unsafe()">实际新闻 <b>正文</b></p>' +
    '&lt;script&gt;hidden()&lt;/script&gt;<div>后续数据 &#x4E2D;文</div>';
  assert.equal(sourceDisplayText(saved), '实际新闻 正文\n后续数据 中文');
  assert.equal(sourceDisplayText('<script>无关闭标签'), '');
});

test('ordinary comparisons and plain source text remain intact', () => {
  assert.equal(sourceDisplayText('增长 < 5%，下降 > 2%，A &amp; B'), '增长 < 5%，下降 > 2%，A & B');
  assert.equal(sourceDisplayText('增长 &lt; 5%'), '增长 < 5%');
  assert.equal(sourceDisplayText('普通新闻摘要'), '普通新闻摘要');
  assert.equal(sourceDisplayText('<table><tr><td>实际</td><td>0</td></tr><tr><td'), '实际 | 0');
  assert.equal(sourceDisplayText('下滑 -5% <\/tr'), '下滑 -5%');
  assert.equal(sourceDisplayText(null), '');
});

test('user input and reports retain intentional HTML examples in file previews', () => {
  const raw = '如何解释 <table><tr><td>0</td></tr></table>？';
  assert.equal(sourceBodyText({ origin: 'user' }, raw), raw);
  assert.equal(sourceBodyText({ content_kind: 'user_input' }, raw), raw);
  assert.equal(sourceBodyText({ kind: 'report' }, raw), raw);
  assert.equal(sourceBodyText({ kind: 'web_search' }, raw), '如何解释\n0\n？');
});
