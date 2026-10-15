const BASE = '/desktop/api/strategy-workshop';

export class WorkshopError extends Error {
  constructor(message, status, code, details) {
    super(message);
    this.name = 'WorkshopError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function workshopRequest(path, { body, ...options } = {}) {
  const headers = { ...options.headers };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      credentials: 'include', ...options, headers,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new WorkshopError('无法连接策略工坊，请检查连接后重试。', 0, 'network_error');
  }
  let data;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) {
    const detail = data?.error;
    throw new WorkshopError(
      (typeof detail === 'string' ? detail : detail?.message) || data?.message || `请求失败（${response.status}）`,
      response.status, data?.code || detail?.code, data?.details || detail?.details,
    );
  }
  if (data === null) throw new WorkshopError('策略工坊返回了无法读取的数据，请重新同步。', response.status, 'invalid_response');
  return data;
}
