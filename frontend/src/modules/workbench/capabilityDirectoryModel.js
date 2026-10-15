import { capabilityStatus } from './capabilityApi.js';

const value = input => String(input ?? '').trim();
const copies = item => Array.isArray(item?.personalCopies) ? item.personalCopies.filter(copy => copy?.assetId) : [];
const identity = (source, id, versionId = '') => `${source}:${value(id)}:${value(versionId)}`;

export function capabilityKind(kind) {
  return kind === 'tool' ? 'tool' : 'skill';
}

export function capabilityName(kind) {
  return capabilityKind(kind) === 'tool' ? 'Tool' : 'Skill';
}

export function personalCapabilityCards(assets = [], kind = 'skill') {
  return assets.filter(item => item?.kind === capabilityKind(kind) && !item.deletedAt).map(item => {
    const versionId = value(item.activeVersionId || item.latestVersionId);
    return {
      key: identity('personal', item.assetId, versionId), source: 'personal', id: value(item.assetId), versionId,
      name: value(item.displayName || item.name) || '未命名能力', description: value(item.description), label: '我的 ' + capabilityName(kind),
      status: capabilityStatus(item), owned: true, raw: item,
    };
  });
}

export function platformCapabilityCards({ official = [], releases = [] } = {}, kind = 'skill') {
  const selectedKind = capabilityKind(kind);
  const officialCards = official.filter(item => item?.kind === selectedKind && !item.deletedAt && item.lifecycle !== 'archived')
    .map(item => {
      const versionId = value(item.latestVersionId), personalCopies = copies(item);
      return {
        key: identity('official', item.assetId, versionId), source: 'official', id: value(item.assetId), versionId,
        name: value(item.displayName || item.name) || '未命名能力', description: value(item.description), label: '平台官方',
        status: '官方版本' + (item.latestVersionNumber ? ` V${item.latestVersionNumber}` : ''),
        owned: item.owned === true || personalCopies.length > 0, raw: item,
      };
    });
  const communityCards = releases.filter(item => item?.kind === selectedKind && item.status === 'published' && !item.withdrawnAt)
    .map(item => {
      const personalCopies = copies(item);
      return {
        key: identity('community', item.releaseId), source: 'community', id: value(item.releaseId), versionId: '',
        name: value(item.title) || '未命名能力', description: value(item.description), label: '用户公开',
        status: `公开版本 ${item.releaseNumber || ''}`.trim(), owned: item.owned === true || personalCopies.length > 0, raw: item,
      };
    });
  return [...officialCards, ...communityCards];
}

export function filterCapabilityCards(cards, query) {
  const needle = value(query).toLocaleLowerCase();
  return needle ? cards.filter(item => [item.name, item.description, item.label, item.status].join(' ').toLocaleLowerCase().includes(needle)) : cards;
}

export function capabilityOwnedTarget(item) {
  if (!item?.owned) return null;
  if (item.source === 'personal') return item.id ? { assetId: item.id, versionId: item.versionId } : null;
  const copy = copies(item.raw)[0];
  return copy ? { assetId: copy.assetId, versionId: copy.versionId || '' } : null;
}

export function capabilityDetail(item, asset, version) {
  const root = item?.source === 'community' ? asset?.root : null;
  const manifest = root?.manifest || version?.manifest || null;
  const issues = Array.isArray(version?.issues) ? version.issues : Array.isArray(asset?.issues) ? asset.issues : [];
  const missingImplementation = manifest?.kind === 'tool' && issues.some(issue => issue?.code === 'MISSING_IMPLEMENTATION');
  return {
    asset, version, manifest,
    sourceIdentity: value(root?.sourceIdentity || manifest?.source?.identity || asset?.sourceIdentity),
    contentDigest: value(root?.contentDigest || version?.contentDigest || asset?.contentDigest),
    files: Array.isArray(manifest?.files) ? manifest.files : Array.isArray(root?.files) ? root.files : [],
    dependencies: Array.isArray(manifest?.dependencies) ? manifest.dependencies : [],
    permissions: manifest?.permissions || root?.permissions || null,
    input: manifest?.tool?.inputSchema || version?.usageGuide?.input || null,
    output: manifest?.tool?.outputSchema || version?.usageGuide?.expected || null,
    executionLabel: missingImplementation ? '待补齐执行实现' : manifest?.tool?.execution?.type || '待配置',
    usage: version?.usageGuide || null,
    owned: item?.source === 'personal' || version?.owned === true || asset?.owned === true || copies(version).length > 0 || copies(asset).length > 0,
    personalCopies: copies(version).length ? copies(version) : copies(asset),
  };
}
