"""Read-only download boundary for all skill-pool detail cards."""

import re
import unicodedata
from urllib.parse import quote

from flask import Response

from .errors import GatewayError


def archive_filename(name):
    """Keep the visible name, with only filesystem-safe bounded characters."""
    value = ''.join('_' if unicodedata.category(char).startswith('C') else char
                    for char in str(name or ''))
    value = re.sub(r'[/\\:*?"<>|]', '_', value).strip(' .') or '未命名资产'
    while len(value.encode('utf-8')) > 235:
        value = value[:-1]
    return value + '.zip'


def standard_export_response(backend, owner, args):
    """Resolve trusted stored content; never accept client-provided definitions."""
    kind = str(args.get('kind') or '')
    source = str(args.get('source') or '')
    asset_id = str(args.get('id') or '')
    version_id = str(args.get('versionId') or '')
    if kind not in {'app', 'skill', 'tool'} or not source or not asset_id:
        raise GatewayError('请选择要导出的资产。', 400)
    if any(len(value) > 256 for value in (source, asset_id, version_id)):
        raise GatewayError('资产标识不正确。', 400)
    if kind == 'app':
        from ..agent_platform.standard_exports import export_standard
        name, archive = export_standard(backend, owner, source, asset_id, version_id)
    else:
        from ..capability_library.standard_exports import export_standard
        name, archive = export_standard(backend.capabilities, owner, kind, source, asset_id, version_id)
    filename = archive_filename(name)
    response = Response(archive, mimetype='application/zip')
    response.headers['Content-Disposition'] = "attachment; filename=\"asset.zip\"; filename*=UTF-8''" + quote(filename, safe='')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
