"""Host-owned immutable result pages, scoped to account, task and configuration."""
import copy
import secrets
import threading
import time
from .contracts import canonical, digest, fail
from .read_budget import current_scope


class ResultPages:
    def __init__(self):
        self.lock = threading.RLock()
        self.entries = {}

    def page(self, owner, version, arguments, value=None, trace=None):
        cursor = arguments.get('cursor')
        query = {k:v for k,v in arguments.items() if k not in ('cursor','offset')}
        identity = digest([owner,current_scope(),version['contentDigest'],version['configurationId'],query])
        with self.lock:
            self.entries = {k:v for k,v in self.entries.items() if v['expires'] > time.monotonic()}
            if cursor:
                entry = self.entries.get(cursor)
                if not entry or entry['identity'] != identity or arguments.get('offset',0):
                    fail('CURSOR_EXPIRED','结果游标失效或条件改变，请重新查询。',409)
                snapshot, offset = entry['snapshot'], entry['offset']
                if trace is not None:
                    trace.extend(copy.deepcopy(snapshot['trace']))
            else:
                if not isinstance(value,dict) or '_page_records' not in value:
                    return value
                value = copy.deepcopy(value)
                rows = value.pop('_page_records')
                dates = value.pop('_page_dates',[])
                size = len(canonical([value,rows,dates]).encode())
                unique = {id(e['snapshot']):e['snapshot'] for e in self.entries.values()}
                if size > 4*1024*1024 or sum(s['size'] for s in unique.values())+size > 32*1024*1024:
                    fail('LIMIT_EXCEEDED','结果快照容量超限，请缩小范围。',413)
                snapshot = {'value':value,'rows':rows,'dates':dates,'size':size,'expires':time.monotonic()+120,
                            'trace':copy.deepcopy(trace or [])}
                offset = arguments.get('offset',0)
            limit = arguments.get('limit',20)
            end = offset+limit
            next_cursor = None
            if end < len(snapshot['rows']):
                next_cursor = entry.get('next') if cursor else None
                if not next_cursor:
                    if len(self.entries)>=6400:
                        fail('READ_BUDGET_EXCEEDED','结果游标数量超限。',429)
                    next_cursor = secrets.token_urlsafe(32)
                    self.entries[next_cursor] = {'identity':identity,'snapshot':snapshot,'offset':end,'expires':snapshot['expires']}
                    if cursor: entry['next'] = next_cursor
            result = copy.deepcopy(snapshot['value'])
            result['records'] = copy.deepcopy(snapshot['rows'][offset:end])
            dates = snapshot['dates'][offset:end]
            result['returned_range'] = {'start':min(dates) if dates else None,'end':max(dates) if dates else None}
            result['pagination'] = {'returned':len(result['records']),'has_more':bool(next_cursor),'next_cursor':next_cursor}
            result['coverage']['retrieval_complete'] = not bool(next_cursor)
            result['coverage']['source_retrieval_complete'] = True
            return result
