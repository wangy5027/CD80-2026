"""Official publication requires reviewed execution evidence for the exact package."""
import json
from pathlib import Path
from .contracts import canonical, digest, fail
from .assertions import sample_passed


def check_release(manifest, evidence=None):
    if evidence is None:
        path = Path(__file__).with_name('official_example_evidence.json')
        evidence = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    examples = manifest.get('examples', [])
    if not examples:
        fail('RELEASE_NOT_VERIFIED', '官方能力发布必须附带示例。', 409)
    for sample in examples:
        record = next((item for item in evidence.get('examples', []) if item.get('manifestDigest') == digest(manifest) and item.get('id') == sample['id']), None)
        if not record or not evidence.get('executionPassed') or not all(evidence.get(k) for k in ('createdAt', 'runnerRevision', 'system')):
            fail('RELEASE_NOT_VERIFIED', '官方示例缺少对应版本的执行证据，请完成发布验收。', 409)
        if not record.get('configurationDigest') or not record.get('contentDigest'):
            fail('RELEASE_NOT_VERIFIED', '官方示例验证环境记录不完整。', 409)
        if manifest['kind'] == 'tool':
            if record.get('status') != 'passed' or not sample_passed(record.get('result', {}).get('content'), sample):
                fail('RELEASE_NOT_VERIFIED', '官方工具示例未通过。', 409)
        elif record.get('status') != 'awaiting_review' or record.get('review', {}).get('decision') != 'accept' or not record.get('review', {}).get('comment'):
            fail('RELEASE_NOT_VERIFIED', '官方 Skill 示例尚未完成效果复核。', 409)
    return True
