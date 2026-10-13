/** Explore-only pipe tables. All other report text stays with the existing safe formatter. */

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
const SEPARATOR = /^:?-{3,}:?$/;

function pipeCells(line) {
  const raw = line.trim();
  if (!raw.includes('|')) return null;
  const cells = [];
  let cell = '';
  let quote = '';
  let codeTicks = 0;
  let linkDepth = 0;
  let separators = 0;
  let finalSeparator = false;

  for (let i = 0; i < raw.length;) {
    const char = raw[i];
    if (char === '\\') {
      let end = i;
      while (raw[end] === '\\') end++;
      const count = end - i;
      if (raw[end] === '|' && count % 2 === 1) {
        cell += '\\'.repeat(Math.floor(count / 2)) + '|';
        i = end + 1;
      } else {
        cell += '\\'.repeat(count);
        i = end;
      }
      finalSeparator = false;
      continue;
    }
    if (char === '`') {
      let end = i;
      while (raw[end] === '`') end++;
      const count = end - i;
      if (!codeTicks) codeTicks = count;
      else if (codeTicks === count) codeTicks = 0;
      cell += raw.slice(i, end);
      i = end;
      finalSeparator = false;
      continue;
    }
    if (!codeTicks) {
      if (quote) {
        if (char === quote) quote = '';
      } else if ((char === '"' || char === "'") && (i === 0 || /[\s([{：:]/u.test(raw[i - 1]))) {
        quote = char;
      } else if (char === '(' && raw[i - 1] === ']') {
        linkDepth = 1;
      } else if (linkDepth && char === '(') {
        linkDepth++;
      } else if (linkDepth && char === ')') {
        linkDepth--;
      }
    }
    if (char === '|' && !quote && !codeTicks && !linkDepth) {
      cells.push(cell.trim());
      cell = '';
      separators++;
      finalSeparator = true;
    } else {
      cell += char;
      finalSeparator = false;
    }
    i++;
  }
  if (quote || codeTicks || linkDepth || !separators) return null;
  cells.push(cell.trim());
  if (raw.startsWith('|')) cells.shift();
  if (finalSeparator) cells.pop();
  return cells.length >= 2 && cells.length <= 20 ? cells : null;
}

function tableAt(lines, start) {
  const header = pipeCells(lines[start]);
  const divider = start + 1 < lines.length ? pipeCells(lines[start + 1]) : null;
  if (!header || !divider || header.length !== divider.length
      || header.some(cell => !cell) || divider.some(cell => !SEPARATOR.test(cell))) return null;
  const rows = [];
  let end = start + 2;
  while (end < lines.length && rows.length < 200) {
    const cells = pipeCells(lines[end]);
    if (!cells || cells.length !== header.length) break;
    rows.push(cells);
    end++;
  }
  // A mismatched first data row means the apparent header is prose, not a table.
  if (!rows.length) return null;
  return { header, rows, end };
}

function renderTable(table, formatter) {
  const cell = value => formatter.formatInline(value);
  const head = `<thead><tr>${table.header.map(value => `<th scope="col">${cell(value)}</th>`).join('')}</tr></thead>`;
  const body = `<tbody>${table.rows.map(row => `<tr>${row.map(value => `<td>${cell(value)}</td>`).join('')}</tr>`).join('')}</tbody>`;
  return `<div class="explore-report-table-wrap" role="region" aria-label="报告表格，可横向滚动" tabindex="0"><table class="explore-report-table">${head}${body}</table></div>`;
}

export function renderReportMarkdown(value, formatter) {
  if (typeof formatter?.renderProse !== 'function' || typeof formatter?.formatInline !== 'function') return '';
  const raw = String(value ?? '');
  const lines = raw.split('\n');
  const parts = [];
  let proseStart = 0;
  let fence = null;

  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (fence) {
      const close = FENCE_CLOSE.exec(line);
      if (close && close[1][0] === fence.mark && close[1].length >= fence.length) fence = null;
      i++;
      continue;
    }
    const open = FENCE_OPEN.exec(line);
    if (open) {
      fence = { mark: open[1][0], length: open[1].length };
      i++;
      continue;
    }
    const table = tableAt(lines, i);
    if (!table) {
      i++;
      continue;
    }
    if (i > proseStart) parts.push(formatter.renderProse(lines.slice(proseStart, i).join('\n')));
    parts.push(renderTable(table, formatter));
    i = table.end;
    proseStart = i;
  }
  if (!parts.length) return formatter.renderProse(raw);
  if (proseStart < lines.length) parts.push(formatter.renderProse(lines.slice(proseStart).join('\n')));
  return parts.join('');
}
