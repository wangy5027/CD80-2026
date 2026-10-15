"""Read snapshots and aggregation against disposable databases."""
import sqlite3
from unittest.mock import patch
from app.bounded_reads import Snapshots
from fastapi import HTTPException
import pytest
from test_api import sample_db, make_client, BASE


def test_snapshot_survives_write_and_rejects_other_scope(make_client, sample_db):
    with make_client() as client:
        body = {'columns':['code','price'], 'page_size':1}
        first = client.post(BASE+'/scan',json=body,headers={'X-CD80-Scope':'alice'})
        assert first.status_code == 200, first.text
        cursor = first.json()['pagination']['next_cursor']
        assert cursor
        with sqlite3.connect(sample_db) as db:
            db.execute('UPDATE cd80_sample SET price=999')
        page = client.post(BASE+'/scan',json={**body,'cursor':cursor},headers={'X-CD80-Scope':'alice'})
        assert page.status_code == 200, page.text
        assert page.json()['data'][0]['price'] != 999
        assert page.json() == client.post(BASE+'/scan',json={**body,'cursor':cursor},headers={'X-CD80-Scope':'alice'}).json()
        assert client.post(BASE+'/scan',json={**body,'cursor':cursor},headers={'X-CD80-Scope':'bob'}).status_code == 409
        assert client.post(BASE+'/scan',json={**body,'cursor':cursor,'columns':['code']},headers={'X-CD80-Scope':'alice'}).status_code == 409


def test_scan_validation_and_aggregate_not_truncated(make_client):
    with make_client() as client:
        assert client.post(BASE+'/scan',json={'columns':['no_such_field']}).status_code == 422
        assert client.post(BASE+'/scan',json={'columns':['code'],'sql':'DROP TABLE'}).status_code == 422
        value=client.post(BASE+'/aggregate',json={'group_by':['code'],'metrics':[{'field':'volume','op':'sum','alias':'total'}],'limit':1})
        assert value.status_code == 429, value.text
        value=client.post(BASE+'/aggregate',json={'metrics':[{'field':'volume','op':'count','alias':'count'}]})
        assert value.status_code == 200, value.text
        assert value.json()['matched_rows'] == value.json()['processed_rows'] == 4


def test_mixed_dates_filter(make_client, sample_db):
    with make_client() as client:
        with sqlite3.connect(sample_db) as db:
            db.execute("UPDATE cd80_sample SET trade_date='20260907.0' WHERE _row_id=1")
            db.execute("UPDATE cd80_sample SET trade_date='2026-10-01' WHERE _row_id=4")
        response=client.post(BASE+'/scan',json={'columns':['code','trade_date'],'filters':[{'field':'trade_date','op':'date_gte','value':'2026-09-01'}]})
        assert response.status_code == 200,response.text
        assert any(r['trade_date']=='20260907.0' for r in response.json()['data'])


def test_bad_source_date_does_not_silently_disappear(make_client):
    with make_client() as client:
        response=client.post(BASE+'/scan',json={'columns':['trade_date'],'filters':[{'field':'trade_date','op':'date_gte','value':'2020-01-01'}]})
        assert response.status_code == 422
        assert response.json()['detail']['code'] == 'DATA_AMBIGUOUS'


def test_expired_cursor_and_oversized_snapshot():
    snapshots=Snapshots()
    with patch('app.bounded_reads.time.monotonic',return_value=100):
        cursor=snapshots.page('scope',{'page_size':1},[{'a':1},{'a':2}])['pagination']['next_cursor']
    with patch('app.bounded_reads.time.monotonic',return_value=221):
        with pytest.raises(HTTPException) as error:
            snapshots.page('scope',{'page_size':1},cursor=cursor)
        assert error.value.detail['code']=='CURSOR_EXPIRED'
    with pytest.raises(HTTPException) as error:
        snapshots.page('scope',{'page_size':1},[{'a':'x'*(4*1024*1024)}])
    assert error.value.detail['code']=='LIMIT_EXCEEDED'


def test_aggregate_sort_only_declared_outputs(make_client):
    with make_client() as client:
        body={'group_by':['code'],'metrics':[{'field':'volume','op':'sum','alias':'total'}],'order_by':[{'field':'total','direction':'desc'}]}
        response=client.post(BASE+'/aggregate',json=body)
        assert response.status_code==200,response.text
        from decimal import Decimal
        values=[Decimal(r['total']) for r in response.json()['data'] if r['total'] is not None]
        assert values==sorted(values,reverse=True)
        body['order_by'][0]['field']='price'
        assert client.post(BASE+'/aggregate',json=body).status_code==422
