"""CSV from authorized mounted JSON; never reads an arbitrary host path."""
import csv
import io
import json
from pathlib import Path


def cell(value):
    text = '' if value is None else json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return "'"+text if text.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')) or text.startswith(('\t','\r','\n')) else text


def run(payload):
    name = payload['file']
    if name.startswith('/input/'):
        name=name[len('/input/'):]
    if Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError('只允许授权输入文件名')
    value = json.loads((Path('/input') / name).read_text(encoding='utf-8'))
    content = value.get('content', value)
    records = content['records']
    if not isinstance(records, list) or len(records) > 10000:
        raise ValueError('记录格式或数量无效')
    if content.get('pagination', {}).get('has_more') or content.get('coverage', {}).get('retrieval_complete') is False:
        raise ValueError('输入尚未读取完整，不能导出为完整数据')
    fields = list(dict.fromkeys(k for row in records for k in row))
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    headers = [cell(k) for k in fields]
    if len(set(headers)) != len(headers):
        raise ValueError('转义后列名冲突，不能丢失列')
    writer.writerow(headers)
    for row in records:
        writer.writerow([cell(row.get(key)) for key in fields])
    data = output.getvalue()
    if len(data.encode('utf-8')) > 4 * 1024 * 1024:
        raise ValueError('CSV超过4MiB')
    Path('/output/result.csv').write_text(data, encoding='utf-8-sig')
    return {'rows': len(records), 'file': 'result.csv', 'source': content.get('source'), 'warnings': content.get('warnings', [])}
