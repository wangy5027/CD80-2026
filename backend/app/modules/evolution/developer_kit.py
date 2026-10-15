"""Self-contained public starter resources; never inspect account storage."""
import json
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ..capability_library.contracts import canonical
from ..capability_library.packages import zip_bytes


def _portable_document(content, source, destination, included):
    """Relocate documentation links without modifying signed starter resources."""
    def replace(match):
        label, target = match.groups()
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path:
            return match.group(0)
        original = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(url.path)))
        if original not in included:
            return f'{label}（完整项目参考，资料包未收录：`{original}`）'
        relative = posixpath.relpath(included[original], posixpath.dirname(destination) or '.')
        suffix = ('?' + url.query if url.query else '') + ('#' + url.fragment if url.fragment else '')
        return f'[{label}]({relative}{suffix})'

    return re.sub(r'\[([^\]\n]+)\]\(([^)\s]+)\)', replace, content.decode('utf-8')).encode('utf-8')


def build():
    root = Path(__file__).resolve().parents[4]
    docs = root / 'docs'
    vision = docs / 'vision-delivery'
    files = {'README.md': (vision / '外部开发指南.md').read_bytes()}
    document_sources = {'README.md': 'docs/vision-delivery/外部开发指南.md'}
    for name in ('csv-summary.application.json', 'research-method.application.json', 'sample-a.csv', 'sample-b.csv', 'pack_capability.py'):
        files[name] = (vision / 'examples' / name).read_bytes()
    # Explicit public resources, no recursive project/data/config directory export.
    for name in ('capability-library/能力包规范.md', 'capability-library/接口规范.md',
                 'capability-library/技术规范.md', 'capability-library/内置工具与Skill接入.md',
                 'capability-library/openapi.yaml', 'capability-library/社区发布设计与技术规范.md',
                 'capability-library/测试体验产品设计与技术规范.md',
                 'capability-library/schemas/capability.schema.json',
                 'agent-platform/应用包与组件复用.md', 'vision-delivery/研究应用与发布接口.md',
                 'asset-authoring/开发与外部接入指南.md', 'asset-authoring/接口与存储契约.md',
                 'asset-authoring/导入导出与运行解耦.md',
                 'local-financial-capabilities/执行协议.md',
                 'local-financial-capabilities/migration/接口与协议设计.md'):
        files['docs/' + name] = (docs / name).read_bytes()
        if name.endswith('.md'):
            document_sources['docs/' + name] = 'docs/' + name
    files['cd80-data-api/docs/CONTRACT.md'] = (root / 'cd80-data-api/docs/CONTRACT.md').read_bytes()
    document_sources['cd80-data-api/docs/CONTRACT.md'] = 'cd80-data-api/docs/CONTRACT.md'
    examples = docs / 'capability-library/examples'
    resources = {
        'decimal-total': ('original/tool.py', 'original/openai-tool.json'),
        'summary-skill': ('original/SKILL.md', 'original/references/method.md'),
    }
    for name, paths in resources.items():
        manifest = json.loads((examples / name / 'capability.json').read_bytes())
        package = {path: (examples / name / path).read_bytes() for path in paths}
        package['capability.json'] = canonical(manifest).encode('utf-8')
        package['provenance.json'] = canonical({'source': manifest['source'], 'files': manifest['files'],
            'note': 'Developer starter; import, test and enable independently.'}).encode('utf-8')
        files['capabilities/' + name + '.zip'] = zip_bytes(package)
        for path, content in package.items():
            files['capabilities/' + name + '/' + path] = content
    files['docs/capability-library/README.md'] = '''# 能力开发资料

这是开发资料包的便携索引。未收录的历史验收、项目进度与背景设计保留原仓库路径说明；
这些材料须在完整项目中查看，不是资料包内的下载链接，也不代表本次下载重新执行了验收。

- [能力包格式](能力包规范.md)与[机器 Schema](schemas/capability.schema.json)
- [统一创作与外部接入](../asset-authoring/开发与外部接入指南.md)、[当前接口](../asset-authoring/接口与存储契约.md)、[原件保存与运行解耦](../asset-authoring/导入导出与运行解耦.md)
- [能力接口](接口规范.md)与[OpenAPI](openapi.yaml)
- [社区发布、精确依赖复制与源码审阅](社区发布设计与技术规范.md)
- [技术规范](技术规范.md)、[测试体验规范](测试体验产品设计与技术规范.md)
- [内置工具与 Skill 接入](内置工具与Skill接入.md)
- [可导入 Tool / Skill 样例与源码](../../capabilities/README.md)
- [固定流程应用包与组件复用](../agent-platform/应用包与组件复用.md)
- [研究应用、沉淀和应用发布接口](../vision-delivery/研究应用与发布接口.md)
- [本地金融执行协议](../local-financial-capabilities/执行协议.md)
- [数据服务契约](../../cd80-data-api/docs/CONTRACT.md)
'''.encode('utf-8')
    files['capabilities/README.md'] = '''# Tool / Skill 开发样例

本资料包精选以下两个可导入样例，并保留完整展开源码。修改后使用根目录
`pack_capability.py` 重新计算摘要并打包；导入后须独立验证和启用。

- 十进制求和 Tool：[导入包](decimal-total.zip)、[清单](decimal-total/capability.json)、[源码](decimal-total/original/tool.py)
- 摘要 Skill：[导入包](summary-skill.zip)、[清单](summary-skill/capability.json)、[方法](summary-skill/original/SKILL.md)、[参考资料](summary-skill/original/references/method.md)

完整项目 `docs/capability-library/examples/` 中的其他规范示例未随包收录，
包括使用无效域名的 MCP 说明样例；不能将它们视为本资料包内可运行的连接。
'''.encode('utf-8')
    included = {name: name for name in files}
    included.update({source: destination for destination, source in document_sources.items()})
    included['docs/capability-library/examples'] = 'capabilities/README.md'
    for destination, source in document_sources.items():
        files[destination] = _portable_document(files[destination], source, destination, included)
    files['README.md'] += '''

## 资料包文档导航

[当前 App / Tool / Skill 创作与外部接入](docs/asset-authoring/开发与外部接入指南.md)
说明统一会话和外部原件保存、导出、准备使用；本资料包原有应用样例保留旧运行时格式。
[能力、应用和社区接口索引](docs/capability-library/README.md)与[Tool / Skill 样例](capabilities/README.md)
可在解压后直接阅读。文中“完整项目参考，资料包未收录”表示历史验收或项目背景资料，
保留的路径用于在完整仓库中查找；不会把这些材料伪装成资料包内可打开的链接。
'''.encode('utf-8')
    return zip_bytes(files)
