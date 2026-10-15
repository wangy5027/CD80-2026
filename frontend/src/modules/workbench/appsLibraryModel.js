// A workspace, a strategy, a modern App and a legacy application have different
// identities and open/copy operations. Never infer ownership from their names.
export const BUILTIN_APPS = Object.freeze([
  { id: 'explore', name: 'Explore', description: '理解事件、发现关联、对照历史，保留研究过程与来源。', workflow: '提出研究问题 → 查看证据与关联 → 保存研究结果', inputs: '研究问题、用户授权的材料与可用数据', outputs: '带来源的研究过程与结果' },
  { id: 'strategy-workshop', name: '策略工坊', description: '把投资想法整理为策略，诊断回测并保存复用。', workflow: '说明想法 → 对齐规则 → 历史数据诊断 → 查看结果', inputs: '策略想法、标的、日期与初始资金', outputs: '规则、回测诊断与个人策略应用' },
  { id: 'app-workshop', name: '应用工坊', description: '按金融需求推荐和组合应用，保存个人应用。', workflow: '说明需求 → 查看推荐 → 组合能力 → 保存应用', inputs: '金融任务需求与可用应用', outputs: '推荐结果与个人应用' },
]);

const text = value => String(value ?? '').trim();
const identity = (source, id, versionId = '') => `${source}:${text(id)}:${text(versionId)}`;
const sourceLabel = Object.freeze({ builtin: '平台内置', asset: '个人 APP', strategy: '个人策略', 'legacy-pipeline': '旧版流程', 'legacy-recipe': '旧版研究', preset: '平台预置', 'community-app': '公开 APP', 'legacy-publication': '旧版共创' });

function card(source, id, name, description, { versionId = '', owned = true, status = '', raw = null, ...extra } = {}) {
  return { key: identity(source, id, versionId), source, id: text(id), versionId: text(versionId),
    name: text(name) || '未命名应用', description: text(description), label: sourceLabel[source], owned: Boolean(owned),
    status: text(status), raw, ...extra };
}

export function personalAppCards({ assets = [], agents = [], strategies = [], recipes = [] } = {}) {
  const builtins = BUILTIN_APPS.map(item => card('builtin', item.id, item.name, item.description, { status: '默认已拥有', raw: item }));
  const modern = assets.map(item => card('asset', item.id, item.name || item.definition?.name, item.description || item.definition?.description,
    { versionId: item.versionId || item.latestVersionId, status: item.verification?.status === 'passed' ? '已验证' : '未验证', raw: item }));
  const strategy = strategies.map(item => card('strategy', item.id, item.name, item.description,
    { versionId: item.versionId || '', status: item.verification?.status === 'passed' ? '已通过工程诊断' : '已保存 · 未验证', raw: item }));
  const pipelines = agents.filter(item => item.personal === true).map(item => card('legacy-pipeline', item.agentRef,
    item.spec?.name, item.spec?.description || item.spec?.goal, { versionId: item.spec?.version, status: '旧版应用', raw: item }));
  const research = recipes.map(item => card('legacy-recipe', item.id, item.name, item.description || item.goalTemplate,
    { status: '旧版应用', raw: item }));
  return [...builtins, ...modern, ...strategy, ...pipelines, ...research];
}

export function platformAppCards({ agents = [], modernReleases = [], legacyReleases = [], personalAssets = [], personalAgents = [], personalRecipes = [] } = {}) {
  const builtins = BUILTIN_APPS.map(item => card('builtin', item.id, item.name, item.description, { status: '默认已拥有', raw: item }));
  const modernCopy = (kind, key, value) => personalAssets.find(item => item.provenance?.kind === kind && text(item.provenance?.[key]) === text(value));
  const modernPublicationCopy = releaseId => personalAssets.find(item => {
    const source = item.provenance;
    return (source?.kind === 'legacy_publication' && text(source.publicationId) === text(releaseId)) ||
      (source?.kind === 'legacy_pipeline_import' && source.source?.kind === 'publication' && text(source.source.id) === text(releaseId));
  });
  const presets = agents.filter(item => item.personal === false).map(item => card('preset', item.agentRef,
    item.spec?.name, item.spec?.description || item.spec?.goal, { versionId: item.spec?.version,
      owned: Boolean(modernCopy('official_legacy_preset', 'agentRef', item.agentRef)), status: '平台预置',
      raw: { ...item, modernCopy: modernCopy('official_legacy_preset', 'agentRef', item.agentRef) || null } }));
  const modern = modernReleases.filter(item => (item.status || 'published') === 'published' && !item.withdrawn)
    .map(item => card('community-app', item.releaseId, item.title, item.description,
      { versionId: item.sourceVersionId, owned: item.owned === true, status: `公开版本 ${item.versionNumber || ''}`.trim(), raw: item }));
  const copiedPublications = new Set([
    ...personalAgents.map(item => item.source).filter(source => source?.kind === 'publication').map(source => text(source.id)),
    ...personalRecipes.map(item => item.source).filter(source => source?.kind === 'publication').map(source => text(source.id)),
  ]);
  const legacy = legacyReleases.filter(item => !item.withdrawn && ['recipe', 'pipeline'].includes(item.kind))
    .map(item => card('legacy-publication', item.id, item.name, item.description,
      { owned: Boolean(modernPublicationCopy(item.id)), status: item.kind === 'recipe' ? '研究应用' : '流程应用',
        raw: { ...item, modernCopy: modernPublicationCopy(item.id) || null,
          legacyCopyExists: Boolean(item.owned || copiedPublications.has(text(item.id))) } }));
  return [...builtins, ...presets, ...modern, ...legacy];
}

export function filterAppCards(items, query) {
  const needle = text(query).toLocaleLowerCase();
  return needle ? items.filter(item => [item.name, item.description, item.label, item.status].join(' ').toLocaleLowerCase().includes(needle)) : items;
}

export function authoredReleases(releases, appId) {
  return releases.filter(item => item.isOwner === true && item.status === 'published' &&
    item.personalCopies?.some(copy => copy.appId === appId));
}

export function targetForOwned(item) {
  if (!item?.owned) return null;
  if (item.source === 'builtin') return { type: 'workspace', view: item.id };
  if (item.source === 'strategy') return { type: 'strategy', app: item.raw };
  if (item.source === 'asset') return { type: 'asset', appId: item.id, versionId: item.versionId };
  if (item.source === 'community-app') {
    const copy = item.raw?.personalCopies?.[0];
    return copy?.appId ? { type: 'asset', appId: copy.appId, versionId: copy.versionId || '' } : null;
  }
  if (['preset', 'legacy-publication'].includes(item.source) && item.raw?.modernCopy?.id)
    return { type: 'asset', appId: item.raw.modernCopy.id, versionId: item.raw.modernCopy.versionId || '' };
  return { type: 'legacy' };
}
