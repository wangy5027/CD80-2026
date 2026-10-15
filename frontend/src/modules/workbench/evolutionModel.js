export function templateParameters(template) {
  return [...new Set([...String(template || '').matchAll(/\{\{\s*([^{}\n]{1,64}?)\s*\}\}/g)].map(match => match[1].trim()))];
}

export function recipeBody(form) {
  const name = String(form.name || '').trim();
  const goalTemplate = String(form.goalTemplate || '').trim();
  if (!name || !goalTemplate) throw new Error('请填写应用名称与任务模板。');
  return { name, description: String(form.description || '').trim(), goalTemplate,
    constraints: String(form.constraints || '').split('\n').map(value => value.trim()).filter(Boolean),
    capabilityRefs: (form.capabilityRefs || []).map(({ assetId, versionId }) => ({ assetId, versionId })),
    ...(form.sourceRunId ? { sourceRunId: form.sourceRunId } : {}) };
}

export function publicationCard(item, source) {
  const capability = source === 'capability';
  return { ...item, key: source + ':' + (item.releaseId || item.id), source,
    id: item.releaseId || item.id, name: item.title || item.name || '未命名成果',
    description: item.description || '', kind: item.kind,
    status: item.withdrawn ? 'withdrawn' : item.status || 'published',
    owned: Boolean(item.isOwner || item.owned), createdAt: item.publishedAt || item.createdAt,
    label: ({ recipe: '研究应用', pipeline: '流程应用', skill: 'Skill · 方法', tool: 'Tool · 执行能力' })[item.kind] || (capability ? '组件' : '应用') };
}

export function publicationDate(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date).replaceAll('/', '-');
}

export function publicationWarnings(preview) {
  const groups = [preview?.warnings, preview?.summary?.warnings];
  return [...new Set(groups.filter(Array.isArray).flat().map(warning => typeof warning === 'string' ? warning : warning?.message)
    .filter(message => typeof message === 'string' && message.trim()))];
}

export function mergeComponents(current, candidates) {
  const merged = new Map(current.map(item => [item.assetId + '@' + item.versionId, item]));
  for (const candidate of candidates || []) {
    const key = candidate.assetId + '@' + candidate.versionId;
    merged.set(key, { ...merged.get(key), ...candidate, capabilityRef: candidate.capabilityRef || candidate });
  }
  return [...merged.values()];
}

// Keep a request identity until the server outcome is known, including body-key APIs.
export function mutationRequester(request, signal) {
  const keys = new Map();
  return async (path, body, { bodyKey = false } = {}) => {
    if (signal.aborted) throw new DOMException('页面已关闭', 'AbortError');
    const signature = path + ':' + JSON.stringify(body);
    if (!keys.has(signature)) keys.set(signature, crypto.randomUUID());
    const key = keys.get(signature);
    try {
      const result = await request(path, { method: 'POST', body: bodyKey ? { ...body, idempotencyKey: key } : body,
        signal, headers: { 'Idempotency-Key': key } });
      if (signal.aborted) throw new DOMException('页面已关闭', 'AbortError');
      keys.delete(signature);
      return result;
    } catch (cause) {
      if (cause.status >= 400 && cause.status < 500 && cause.code !== 'OUTCOME_UNKNOWN') keys.delete(signature);
      throw cause;
    }
  };
}

// Reuse preparation too: the first attempt may have changed the server's duplicate state.
export function preparedMutationRequester(mutate) {
  const pending = new Map();
  return async (path, prepare) => {
    if (!pending.has(path)) {
      const body = await prepare();
      if (body == null) return null;
      pending.set(path, structuredClone(body));
    }
    try {
      const result = await mutate(path, pending.get(path));
      pending.delete(path);
      return result;
    } catch (cause) {
      if (cause.status >= 400 && cause.status < 500 && cause.code !== 'OUTCOME_UNKNOWN') pending.delete(path);
      throw cause;
    }
  };
}

export function dependencyBindingKey(dependency, packageKind) {
  return dependency.bindingId || (packageKind === 'recipe' ? dependency.sourceIdentity
    : dependency.kind ? dependency.kind + ':' + dependency.componentId : dependency.componentId);
}

export function selectedRecipeComponents(refs, catalog) {
  return refs.map(reference => {
    const exact = catalog.find(item => item.assetId === reference.assetId && item.versionId === reference.versionId);
    const current = exact || catalog.find(item => item.assetId === reference.assetId);
    return { ...reference, name: current?.name || '目录中不可用的组件', missing: !current,
      status: !current ? '已删除、归档或当前无权访问，可取消后选择其他组件。'
        : !exact ? '保留已锁定的历史版本，运行时会重新检查。'
          : current.available ? '已验证并启用' : current.reason || '尚不可用，请验证并启用或选择其他组件。',
      replacement: !exact && current?.available ? current : null };
  });
}

export async function allPages(request, path, { signal, isCurrent = () => true } = {}) {
  const items = [], seen = new Set();
  let cursor = '';
  do {
    if (signal?.aborted || !isCurrent()) throw new DOMException('目录请求已过期', 'AbortError');
    const page = await request(path + (cursor ? (path.includes('?') ? '&' : '?') + 'cursor=' + encodeURIComponent(cursor) : ''), { signal });
    if (signal?.aborted || !isCurrent()) throw new DOMException('目录请求已过期', 'AbortError');
    items.push(...(page.items || []));
    cursor = page.nextCursor || '';
    if (cursor && seen.has(cursor)) throw new Error('目录分页未能继续，请刷新后重试。');
    seen.add(cursor);
  } while (cursor);
  return { items };
}
