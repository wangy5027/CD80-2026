"""Generate explicit connector contracts from the checked-in data catalog."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLES = ('daily etf_fund_daily index_dd anns_d margin_data stock_basic etf_basic fund_basic daily_basic '
          'stock_fin_indicator stock_fin_income stock_fin_cashflow stock_fin_balancesheet lhb_summary '
          'cn_cpi cn_lpr yc_cb ipo stock_fin_dividend bond_cb_redeem cb_price_chg bond_cb_basic ci_index_member').split()


def build():
    module = ROOT / 'backend/app/modules/capability_library'
    for path in [module / 'capability.schema.json', ROOT / 'docs/capability-library/schemas/capability.schema.json']:
        schema = json.loads(path.read_text(encoding='utf-8'))
        def visit(value):
            if isinstance(value, dict):
                props = value.get('properties', {})
                if props.get('type') == {'const': 'python_connector'}:
                    props['protocolVersion'] = {'type': 'string', 'enum': ['1', '2']}
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(schema)
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    path = module / 'data_connection_registry.json'
    registry = json.loads(path.read_text(encoding='utf-8'))
    catalog = json.loads((ROOT / 'cd80-data-api/docs/data_catalog.json').read_text(encoding='utf-8'))
    tables = ['cd80_' + t for t in TABLES]
    fields = sorted({f['name'] for t in catalog['tables'] if t['name'] in tables for f in t['fields']} | {'_row_id'})
    field = {'type': 'string', 'enum': fields}
    filters = copy.deepcopy(registry['cd80.local-data']['query']['schema']['properties']['filters'])
    filters['items']['properties']['field'] = field
    filters['items']['properties']['op']['enum'] += ['date_gte', 'date_lte']
    props = {'table': {'enum': tables}, 'columns': {'type': 'array', 'minItems': 1, 'maxItems': 40, 'items': field},
             'filters': filters, 'page_size': {'type': 'integer', 'minimum': 1, 'maximum': 100},
             'cursor': {'type': ['string', 'null'], 'maxLength': 256},
             'order_by': {'type': 'array', 'maxItems': 2, 'items': {'type': 'object', 'properties': {'field': field, 'direction': {'enum': ['asc', 'desc']}}, 'required': ['field', 'direction'], 'additionalProperties': False}}}
    registry['cd80.local-data']['query_v2'] = {'method': 'POST', 'path': '/desktop/api/financial-data/tables/{table}/scan', 'pathFields': ['table'],
        'schema': {'type': 'object', 'properties': props, 'required': ['table', 'columns', 'filters', 'order_by'], 'additionalProperties': False}}
    lookup = copy.deepcopy(registry['cd80.local-data']['query'])
    lookup['schema']['properties']['table'] = {'enum': tables}
    lookup['schema']['properties']['columns']['items'] = field
    lookup['schema']['properties']['sort_by'] = field
    lookup['schema']['properties']['filters'] = filters
    lookup['schema']['properties']['filters'] = copy.deepcopy(filters)
    lookup['schema']['properties']['filters']['items']['properties']['op']['enum'] = ['eq', 'gte', 'lte', 'contains']
    registry['cd80.local-data']['lookup_v2'] = lookup
    metric = {'type': 'object', 'properties': {'field': field, 'op': {'enum': ['count', 'sum', 'min', 'max']}, 'alias': {'type': 'string', 'maxLength': 40}}, 'required': ['field', 'op', 'alias'], 'additionalProperties': False}
    registry['cd80.local-data']['aggregate_v1'] = {'method': 'POST', 'path': '/desktop/api/financial-data/tables/{table}/aggregate', 'pathFields': ['table'],
        'schema': {'type': 'object', 'properties': {'table': {'enum': tables}, 'filters': filters, 'group_by': {'type': 'array', 'maxItems': 3, 'items': field},
          'metrics': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': metric}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'required': ['table', 'metrics'], 'additionalProperties': False}}
    registry['cd80.local-data']['aggregate_v1']['schema']['properties']['filters'] = lookup['schema']['properties']['filters']
    registry['cd80.local-data']['aggregate_v1']['schema']['properties']['order_by'] = {'type':'array','maxItems':2,'items':{'type':'object','properties':{'field':{'type':'string','maxLength':128},'direction':{'enum':['asc','desc']}},'required':['field','direction'],'additionalProperties':False}}
    table_fields = {t['name']:[f['name'] for f in t['fields']] for t in catalog['tables'] if t['name'] in tables}
    for name in ('query_v2','lookup_v2','aggregate_v1'):
        registry['cd80.local-data'][name]['tableFields'] = table_fields
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    build()
