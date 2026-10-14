const namedEntities = Object.freeze({
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
  hellip: '…', mdash: '—', ndash: '–', bull: '•', times: '×',
  copy: '©', reg: '®', trade: '™', laquo: '«', raquo: '»',
});

function decodeOnce(value) {
  return value.replace(/&(#(?:x[\da-f]+|\d+)|[a-z][a-z\d]+);/gi, (match, entity) => {
    if (entity[0] !== '#') return namedEntities[entity.toLowerCase()] ?? match;
    const code = entity[1]?.toLowerCase() === 'x'
      ? Number.parseInt(entity.slice(2), 16) : Number.parseInt(entity.slice(1), 10);
    return Number.isInteger(code) && code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff)
      ? String.fromCodePoint(code) : match;
  });
}

/** Convert saved source snippets to readable text; the result is always rendered as Vue text. */
export function sourceDisplayText(value) {
  if (typeof value !== 'string' || !value) return '';
  let input = value;
  // Older records can contain literal HTML, escaped HTML, or a double-escaped copy.
  for (let pass = 0; pass < 3; pass += 1) {
    const decoded = decodeOnce(input);
    if (decoded === input) break;
    input = decoded;
  }
  input = input.replace(/\r\n?/g, '\n')
    .replace(/<\s*(script|style)\b(?:[^>"']|"[^"]*"|'[^']*')*>[\s\S]*?(?:<\s*\/\s*\1\s*>|$)/gi, '')
    .replace(/<!--[\s\S]*?(?:-->|$)/g, '')
    .replace(/<![^>]*>|<\?[^>]*\?>/g, '');

  const tags = /<\/?([a-z][a-z\d:-]*)\b(?:[^>"']|"[^"]*"|'[^']*')*>/gi;
  let output = '';
  let position = 0;
  let tableDepth = 0;
  for (const match of input.matchAll(tags)) {
    output += input.slice(position, match.index);
    const tag = match[1].toLowerCase();
    const closing = /^<\//.test(match[0]);
    if (tag === 'table') {
      tableDepth = Math.max(0, tableDepth + (closing ? -1 : 1));
      output += '\n';
    } else if (tag === 'tr') output += '\n';
    else if (tag === 'td' || tag === 'th') { if (closing) output += ' | '; }
    else if (tag === 'br') output += tableDepth ? ' ' : '\n';
    else if (['p', 'div', 'li', 'ul', 'ol', 'section', 'article', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'].includes(tag)) output += tableDepth ? ' ' : '\n';
    position = match.index + match[0].length;
  }
  output += input.slice(position);
  // Saved snippets may stop mid-tag at their character limit.
  output = output.replace(/<\/?(?:table|tr|th|td|tbody|thead|tfoot|div|p|span|a|br|ul|ol|li|script|style)\b[^<>]*$/i, '');
  return output.split('\n')
    .map(line => line.replace(/[\t\u00a0\u200b\u3000 ]+/g, ' ').trim()
      .replace(/^(?:\|\s*)+|(?:\s*\|)+$/g, '').trim())
    .filter(line => line && !line.split('|').every(cell => /^[-–—]{1,4}$/.test(cell.trim())))
    .join('\n');
}

/** User-provided text and reports may intentionally contain HTML examples. */
export function sourceBodyText(record, value) {
  return record?.kind === 'report' || record?.kind === 'user' || record?.content_kind === 'user_input' || record?.origin === 'user'
    ? (typeof value === 'string' ? value : '')
    : sourceDisplayText(value);
}
