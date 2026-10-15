"""Maintainer-owned public catalog. Imported packages cannot register publications."""
import json
from pathlib import Path
from .contracts import digest
from .packages import parse_package, file_index
from .releases import check_release
from .service import PUBLIC


def seed_catalog(library):
    root = Path(__file__).resolve().parents[4]/'capabilities'
    catalog = root/'public-catalog.json'
    if not catalog.exists(): return []
    results = []
    for item in json.loads(catalog.read_text(encoding='utf-8'))['packages']:
        try:
            paths = [(root/item[key]).resolve() for key in ('package','evidence')]
            if any(not p.is_relative_to(root.resolve()) for p in paths): raise ValueError('catalog path outside capabilities')
            package, evidence_path = paths
            raw = package.read_bytes()
            manifest, files = parse_package(raw, package.name)
            evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
            check_release(manifest, evidence)
            content = digest({'manifest':{**manifest,'files':file_index(files)},'files':file_index(files)})
            records = [r for r in evidence['examples'] if r.get('manifestDigest')==digest(manifest)]
            if any(r.get('contentDigest')!=content for r in records): raise ValueError('evidence content mismatch')
            library.import_package(PUBLIC, raw, package.name, release_evidence=evidence)
            results.append({'package':item['package'],'status':'published'})
        except Exception as error:
            # One unsuccessful candidate must not disable the platform or other capabilities.
            results.append({'package':item.get('package'),'status':'rejected','reason':str(error)})
    return results
