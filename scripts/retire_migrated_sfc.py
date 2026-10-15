"""Remove only the two approved legacy adapters after verified replacement publication."""
import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    catalog=json.loads((ROOT/'capabilities/public-catalog.json').read_text(encoding='utf-8'))
    required={'sfc_history','sfc-history','sfc_announcements','sfc-announcement'}
    if not required.issubset(catalog.get('retiredCapabilities',[])):
        raise RuntimeError('Publish verified replacements before retirement')
    path=ROOT/'backend/app/modules/workbench/sfc_tools.py'
    source=path.read_text(encoding='utf-8');tree=ast.parse(source);spans=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name in ('_history','_announcements'):
            spans.append((node.lineno,node.end_lineno))
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_TOOL_DEFS' for t in node.targets):
            for item in node.value.elts:
                if item.elts[0].value in ('sfc_history','sfc_announcements'):spans.append((item.lineno,item.end_lineno))
    lines=source.splitlines(keepends=True)
    for start,end in sorted(spans,reverse=True):del lines[start-1:end]
    updated=''.join(lines);compile(updated,str(path),'exec');path.write_text(updated,encoding='utf-8')
    path=ROOT/'backend/app/modules/workbench/sfc_client.py'
    source=path.read_text(encoding='utf-8')
    blocked=('/terminal/rest/company/notice','/terminal/rest/company/notice/detail','/terminal/quotes/api/dayk','/terminal/quotes/api/weekk','/terminal/quotes/api/monthk')
    source=''.join(line for line in source.splitlines(keepends=True) if not any('"'+name+'"' in line for name in blocked))
    compile(source,str(path),'exec');path.write_text(source,encoding='utf-8')
    for name in ('sfc-history','sfc-announcement'):
        path=ROOT/'skills'/name/'SKILL.md'
        if path.exists():path.unlink()
    print('Retired only history and announcement; other SFC adapters and historical records preserved.')


if __name__=='__main__':main()
