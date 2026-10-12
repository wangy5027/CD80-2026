/* A deliberately small, escaped message formatter. It never accepts raw HTML or links. */
(() => {
  'use strict';

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));

  function parseSegments(value) {
    const lines = String(value ?? '').replace(/\r\n?/g, '\n').split('\n');
    const segments = [];
    let prose = [];
    let code = [];
    let fence = null;
    const pushProse = () => {
      if (prose.length) segments.push({ type: 'text', text: prose.join('\n') });
      prose = [];
    };
    for (const line of lines) {
      if (fence) {
        const close = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
        if (close && close[1][0] === fence.mark && close[1].length >= fence.length) {
          segments.push({ type: 'code', language: fence.language, code: code.join('\n') });
          code = [];
          fence = null;
        } else {
          code.push(line);
        }
      } else {
        const open = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
        if (open) {
          pushProse();
          fence = { mark: open[1][0], length: open[1].length, language: open[2].trim().slice(0, 60) };
        } else {
          prose.push(line);
        }
      }
    }
    if (fence) segments.push({ type: 'code', language: fence.language, code: code.join('\n') });
    pushProse();
    return segments;
  }

  function formatInline(value, allowStrong = true) {
    const text = String(value ?? '');
    let result = '';
    let plain = '';
    const flush = () => { result += escapeHtml(plain); plain = ''; };
    for (let i = 0; i < text.length;) {
      if (text[i] === '`') {
        const end = text.indexOf('`', i + 1);
        if (end > i + 1 && !text.slice(i + 1, end).includes('\n')) {
          flush();
          result += `<code>${escapeHtml(text.slice(i + 1, end))}</code>`;
          i = end + 1;
          continue;
        }
      }
      if (allowStrong && text.startsWith('**', i)) {
        const end = text.indexOf('**', i + 2);
        if (end > i + 2 && !text.slice(i + 2, end).includes('\n')) {
          flush();
          result += `<strong>${formatInline(text.slice(i + 2, end), false)}</strong>`;
          i = end + 2;
          continue;
        }
      }
      plain += text[i];
      i++;
    }
    flush();
    return result;
  }

  function renderProse(value) {
    const lines = String(value ?? '').split('\n');
    const blocks = [];
    let paragraph = [];
    let list = null;
    const flushParagraph = () => {
      if (paragraph.length) blocks.push(`<p>${formatInline(paragraph.join('\n'))}</p>`);
      paragraph = [];
    };
    const flushList = () => {
      if (list) blocks.push(`<${list.tag}${list.tag === 'ol' ? ` start="${list.start}"` : ''}>${list.items.map(text => `<li>${formatInline(text)}</li>`).join('')}</${list.tag}>`);
      list = null;
    };
    for (const line of lines) {
      const heading = /^ {0,3}(#{1,4})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/.exec(line);
      const item = /^ {0,3}(?:([-+*])|([0-9]{1,9})[.)])[ \t]+(.+)$/.exec(line);
      const quote = /^ {0,3}>[ \t]?(.*)$/.exec(line);
      if (heading || quote || /^ {0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$/.test(line)) {
        flushParagraph(); flushList();
        if (heading) {
          // Message headings stay below the page's own heading in the hierarchy.
          const level = Math.min(heading[1].length + 2, 6);
          blocks.push(`<h${level}>${formatInline(heading[2])}</h${level}>`);
        } else if (quote) blocks.push(`<blockquote>${formatInline(quote[1])}</blockquote>`);
        else blocks.push('<hr>');
      } else if (item) {
        flushParagraph();
        const tag = item[1] ? 'ul' : 'ol';
        if (list?.tag !== tag) { flushList(); list = { tag, start: tag === 'ol' ? Number(item[2]) : 0, items: [] }; }
        list.items.push(item[3]);
      } else if (!line.trim()) {
        flushParagraph(); flushList();
      } else {
        flushList(); paragraph.push(line);
      }
    }
    flushParagraph(); flushList();
    return blocks.join('');
  }

  function renderAssistant(value, messageIndex) {
    const safeIndex = Number.isSafeInteger(messageIndex) && messageIndex >= 0 ? messageIndex : 0;
    let codeIndex = 0;
    return parseSegments(value).map((segment) => {
      if (segment.type === 'text') return `<div class="message-prose">${renderProse(segment.text)}</div>`;
      const index = codeIndex++;
      return `<div class="fenced-code"><div class="code-head"><span>${escapeHtml(segment.language || '代码')}</span><button type="button" data-action="copy-code" data-message-index="${safeIndex}" data-code-index="${index}" aria-label="复制代码">复制代码</button></div><pre><code>${escapeHtml(segment.code)}</code></pre></div>`;
    }).join('');
  }

  function codeBlockAt(value, index) {
    if (!Number.isSafeInteger(index) || index < 0) return null;
    return parseSegments(value).filter((segment) => segment.type === 'code')[index]?.code ?? null;
  }

  const formatter = Object.freeze({ escapeHtml, parseSegments, formatInline, renderProse, renderAssistant, codeBlockAt });
  if (typeof module !== 'undefined' && module.exports) module.exports = formatter;
  if (typeof window !== 'undefined') window.FicagentFormat = formatter;
})();
