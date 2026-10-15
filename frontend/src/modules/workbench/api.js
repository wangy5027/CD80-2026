const API = '/desktop/api';

export async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body !== 'string') {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`${API}${path}`, { credentials: 'include', ...options, headers, body });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('无法连接网站后端，请确认 python main.py 正在运行。');
  }
  const type = response.headers.get('content-type') || '';
  let data = null;
  try { data = type.includes('json') ? await response.json() : await response.text(); } catch (_) { /* empty response */ }
  if (!response.ok) {
    const error = new Error(data && (data.error || data.message) || `请求失败（${response.status}）`);
    error.status = response.status; error.code = data?.code; error.details = data?.details;
    throw error;
  }
  return data;
}

export async function fileResponse(id, signal) {
  const response = await fetch(`${API}/files/${encodeURIComponent(id)}`, { credentials: 'include', signal });
  if (!response.ok) throw new Error(`文件读取失败（${response.status}）`);
  return response;
}

export async function chatResponse(body, signal) {
  let response;
  try {
    response = await fetch(`${API}/chat`, {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal,
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('无法连接网站后端，请确认 python main.py 正在运行。');
  }
  if (!response.ok) {
    let data = null;
    try { data = await response.json(); } catch (_) { /* empty response */ }
    throw new Error(data?.error || `发送失败（${response.status}）`);
  }
  if (!response.body) throw new Error('网站后端没有返回对话数据。');
  return response;
}

export async function* readSse(stream) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let lineBuffer = '';
  let eventLines = [];
  let completed = false;
  const emit = () => {
    if (!eventLines.length) return null;
    const lines = eventLines;
    eventLines = [];
    const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() || 'message';
    const payload = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
    if (!payload) return null;
    let data;
    try { data = JSON.parse(payload); } catch (_) { data = { text: payload }; }
    return { event, data };
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) { completed = true; break; }
      lineBuffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = lineBuffer.indexOf('\n')) >= 0) {
        const line = lineBuffer.slice(0, end).replace(/\r$/, '');
        lineBuffer = lineBuffer.slice(end + 1);
        if (line) eventLines.push(line);
        else {
          const item = emit();
          if (item) yield item;
        }
      }
    }
    lineBuffer += decoder.decode();
    if (lineBuffer) eventLines.push(lineBuffer.replace(/\r$/, ''));
    const item = emit();
    if (item) yield item;
  } finally {
    if (!completed) { try { await reader.cancel(); } catch (_) { /* aborted stream */ } }
    try { reader.releaseLock(); } catch (_) { /* released stream */ }
  }
}
