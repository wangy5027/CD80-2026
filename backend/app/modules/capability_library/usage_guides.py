"""Version-owned examples and exact-digest compatibility for old official packages."""
import json
from pathlib import Path
from .contracts import digest


def usage_examples(version):
    examples = version['manifest'].get('examples', [])
    source = '作者随版本提供；预期结果不代表已在你的环境验证'
    if not examples:
        legacy = json.loads(Path(__file__).with_name('legacy_usage_guides.json').read_text(encoding='utf-8'))
        guide = legacy.get(digest(version['manifest']))
        examples = [{**guide, 'id': 'legacy', 'requirements': [], 'filePaths': []}] if guide else []
        source = '平台维护的旧版示例（精确内容匹配）'
    return [{**item, 'source': source, 'versionId': version['versionId']} for item in examples]


def usage_guide(version):
    return next(iter(usage_examples(version)), None)
