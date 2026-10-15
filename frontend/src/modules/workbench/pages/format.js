/* Escaped message markup; only known search sources can become citation buttons. */
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

  function sourceUrl(value) {
    if (typeof value !== 'string' || value.length > 2048 || /[\u0000-\u0020\u007f]/u.test(value)) return null;
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
      return url;
    } catch { return null; }
  }

  function sourceContext(sources) {
    const context = { byId: new Map(), byUrl: new Map() };
    if (!Array.isArray(sources)) return context;
    for (const [index, source] of sources.slice(0, 20).entries()) {
      const url = sourceUrl(source?.url);
      if (!url) continue;
      const entry = { number: index + 1, url: url.href, host: url.hostname.replace(/^www\./, ''), title: typeof source.title === 'string' ? source.title.slice(0, 240) : url.hostname };
      url.hash = '';
      if (!context.byUrl.has(url.href)) context.byUrl.set(url.href, entry);
      if (/^S[1-9][0-9]*$/.test(source.id) && !context.byId.has(source.id)) context.byId.set(source.id, entry);
    }
    return context;
  }

  function findSource(context, value) {
    const url = sourceUrl(value);
    if (!context || !url) return null;
    url.hash = '';
    return context.byUrl.get(url.href) || null;
  }

  function citation(source) {
    return `<button type="button" class="message-citation" data-action="open-source" data-source-url="${escapeHtml(source.url)}" title="${escapeHtml(source.title + ' · ' + source.host)}" aria-label="${escapeHtml('查看来源 ' + source.number + '：' + source.title)}">${source.number}</button>`;
  }

  function markdownLinkAt(text, start) {
    if (text[start] !== '[' || text[start - 1] === '!') return null;
    const labelEnd = text.indexOf(']', start + 1);
    if (labelEnd < 0 || text[labelEnd + 1] !== '(' || labelEnd - start > 500 || /[\n\[]/.test(text.slice(start + 1, labelEnd))) return null;
    const urlStart = labelEnd + 2;
    let depth = 1;
    for (let i = urlStart; i < Math.min(text.length, urlStart + 2049); i++) {
      if (text[i] === '\n') break;
      if (text[i] === '(') depth++;
      if (text[i] === ')' && --depth === 0) {
        return { label: text.slice(start + 1, labelEnd), url: text.slice(urlStart, i), end: i + 1 };
      }
    }
    return null;
  }

  function formatInline(value, allowStrong = true, sources = null) {
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
          result += `<strong>${formatInline(text.slice(i + 2, end), false, sources)}</strong>`;
          i = end + 2;
          continue;
        }
      }
      if (sources && text[i] === '[') {
        const link = markdownLinkAt(text, i);
        const source = link && findSource(sources, link.url);
        if (source) {
          flush();
          // Keep descriptive link text; generic labels and raw URLs add no value.
          const generic = /^(?:链接|来源|原文|查看原文|详情|参考|link|source|here|\d+)$/i.test(link.label.trim()) || link.label === link.url;
          result += (generic ? '' : formatInline(link.label, false)) + citation(source);
          i = link.end;
          continue;
        }
        const reference = /^\[(S[1-9][0-9]*)\](?!\()/.exec(text.slice(i));
        if (reference && sources.byId.has(reference[1])) {
          flush(); result += citation(sources.byId.get(reference[1]));
          i += reference[0].length;
          continue;
        }
      }
      if (sources && /^https?:\/\//.test(text.slice(i, i + 8)) && (i === 0 || /[\s(（：:]/u.test(text[i - 1]))) {
        const candidate = /^https?:\/\/[^\s<>"'`]+/.exec(text.slice(i));
        let rawUrl = candidate?.[0].replace(/[.,;!?。，；！？、）\]】]+$/u, '');
        while (rawUrl?.endsWith(')') && (rawUrl.match(/\)/g) || []).length > (rawUrl.match(/\(/g) || []).length) rawUrl = rawUrl.slice(0, -1);
        const source = rawUrl && findSource(sources, rawUrl);
        if (source) {
          flush(); result += citation(source);
          i += rawUrl.length;
          continue;
        }
      }
      plain += text[i];
      i++;
    }
    flush();
    return result;
  }

  function renderProse(value, sources = null) {
    const lines = String(value ?? '').split('\n');
    const blocks = [];
    let paragraph = [];
    let list = null;
    const flushParagraph = () => {
      if (paragraph.length) blocks.push(`<p>${formatInline(paragraph.join('\n'), true, sources)}</p>`);
      paragraph = [];
    };
    const flushList = () => {
      if (list) blocks.push(`<${list.tag}${list.tag === 'ol' ? ` start="${list.start}"` : ''}>${list.items.map(text => `<li>${formatInline(text, true, sources)}</li>`).join('')}</${list.tag}>`);
      list = null;
    };
    const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map(x => x.trim().replace(/\\\|/g, '|'));
    for (let row = 0; row < lines.length; row++) {
      const line = lines[row];
      const headers = line.includes('|') ? cells(line) : [];
      const separators = cells(lines[row + 1] || '');
      if (headers.length > 1 && separators.length === headers.length && separators.every(x => /^:?-{3,}:?$/.test(x))) {
        flushParagraph(); flushList();
        row++;
        const body = [];
        while (row + 1 < lines.length && lines[row + 1].includes('|')) {
          const values = cells(lines[row + 1]);
          if (values.length !== headers.length) break;
          row++;
          body.push(`<tr>${values.map(x => `<td>${formatInline(x, true, sources)}</td>`).join('')}</tr>`);
        }
        blocks.push(`<div class="message-table-scroll"><table><thead><tr>${headers.map(x => `<th scope="col">${formatInline(x, true, sources)}</th>`).join('')}</tr></thead><tbody>${body.join('')}</tbody></table></div>`);
        continue;
      }
      const heading = /^ {0,3}(#{1,4})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/.exec(line);
      const item = /^ {0,3}(?:([-+*])|([0-9]{1,9})[.)])[ \t]+(.+)$/.exec(line);
      const quote = /^ {0,3}>[ \t]?(.*)$/.exec(line);
      if (heading || quote || /^ {0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$/.test(line)) {
        flushParagraph(); flushList();
        if (heading) {
          // Message headings stay below the page's own heading in the hierarchy.
          const level = Math.min(heading[1].length + 2, 6);
          blocks.push(`<h${level}>${formatInline(heading[2], true, sources)}</h${level}>`);
        } else if (quote) blocks.push(`<blockquote>${formatInline(quote[1], true, sources)}</blockquote>`);
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

  function renderAssistant(value, messageIndex, sources = []) {
    const safeIndex = Number.isSafeInteger(messageIndex) && messageIndex >= 0 ? messageIndex : 0;
    let codeIndex = 0;
    const context = sourceContext(sources);
    return parseSegments(value).map((segment) => {
      if (segment.type === 'text') return `<div class="message-prose">${renderProse(segment.text, context)}</div>`;
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
