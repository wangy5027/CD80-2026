// Same-origin AkShare news API. Authentication uses the existing session cookie.
import { request } from './api.js';

const SOURCES = new Set(['eastmoney', 'cls']);
const QUERY_KEYS = new Set(['source', 'limit', 'offset', 'q']);

function newsQuery(options = {}) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new Error('新闻查询参数必须是对象。');
  }
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(options)) {
    if (!QUERY_KEYS.has(key)) throw new Error(`不支持的新闻查询参数：${key}`);
    if (value === undefined || value === null) continue;
    if (key === 'source') {
      if (!SOURCES.has(value)) throw new Error('新闻来源仅支持 eastmoney 或 cls。');
    } else if (key === 'limit') {
      if (!Number.isInteger(value) || value < 1 || value > 100) {
        throw new Error('新闻 limit 必须是 1 到 100 的整数。');
      }
    } else if (key === 'offset') {
      if (!Number.isInteger(value) || value < 0 || value > 1000) {
        throw new Error('新闻 offset 必须是 0 到 1000 的整数。');
      }
    } else if (key === 'q') {
      if (typeof value !== 'string' || value.length > 80 || /[\u0000-\u001f\u007f]/.test(value)) {
        throw new Error('新闻关键词必须是最多 80 字符的纯文本。');
      }
    }
    params.set(key, String(value));
  }
  return params.size ? `?${params}` : '';
}

export const getNewsSources = (signal) => request('/news/sources', { signal });
export const getNews = (options = {}, signal) => request(`/news${newsQuery(options)}`, { signal });
