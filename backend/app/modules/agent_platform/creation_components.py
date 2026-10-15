"""Small, owner-local component catalog for drafting Apps, not runtime admission."""
import copy

from ..capability_library.components import build_snapshot
from ..capability_library.contracts import CapabilityError, canonical
from ..capability_library.packages import has_secret
from ..workbench.errors import GatewayError

MAX_COMPONENTS = 12
MAX_ITEM_CHARS = 5000
MAX_CATALOG_CHARS = 36000
STATIC_READINESS = {'unverified', 'verified', 'ready'}


def catalog(library, owner):
    """Expose precise private versions suitable for design without enabling them.

    The returned descriptions and schemas are model context. Source files,
    credentials and complete snapshots stay server-side until a chosen exact
    version is frozen by AssetApps._freeze. Runtime admission remains separate.
    """
    if library is None:
        return []
    result, remaining = [], MAX_CATALOG_CHARS
    for asset in library.store.list(owner, 'asset'):
        if (asset.get('deletedAt') or asset.get('lifecycle') != 'active' or
                asset.get('kind') not in {'skill', 'tool'}):
            continue
        versions = dict.fromkeys(filter(None, (asset.get('latestVersionId'), asset.get('activeVersionId'))))
        for version_id in versions:
            try:
                version = library.version(owner, asset['assetId'], version_id)
                manifest = version['manifest']
                if (manifest.get('kind') != asset['kind'] or version.get('containsSecret') or
                        version.get('contentValidity') != 'valid' or
                        version.get('readiness') not in STATIC_READINESS or version.get('issues')):
                    continue
                # Check file and dependency integrity without granting execution.
                build_snapshot(library, owner, asset['assetId'], version_id)
                item = {'componentRef': {'assetId': asset['assetId'], 'versionId': version_id},
                        'kind': asset['kind'], 'name': manifest['name'],
                        'description': manifest.get('description', ''),
                        'runtimeStatus': {'readiness': version['readiness'],
                                          'enabled': bool(asset.get('enabled') and asset.get('activeVersionId') == version_id)},
                        'designOnly': True}
                if asset['kind'] == 'tool':
                    tool = manifest['tool']
                    item.update(inputSchema=copy.deepcopy(tool['inputSchema']),
                                outputSchema=copy.deepcopy(tool.get('outputSchema', {'type': 'object'})),
                                inputFiles=bool(manifest['permissions']['inputFiles']))
                else:
                    item.update(taskRequired=True, taskHint=manifest.get('description', ''))
                encoded = canonical(item)
                size = len(encoded)
                if size > MAX_ITEM_CHARS or size > remaining or has_secret({'catalog': encoded.encode('utf-8')}):
                    continue
            except (CapabilityError, GatewayError, KeyError, TypeError, ValueError):
                continue
            result.append(item)
            remaining -= size
            if len(result) >= MAX_COMPONENTS:
                return result
    return result
