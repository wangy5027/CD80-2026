"""Apply explicit reviewer decisions to the disposable validation account, then chat-test."""
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
from backend.app import create_app
from scripts.validation_suite_helpers import require_suite_review


def main():
    acceptance_password = os.environ.get("CD80_ACCEPTANCE_PASSWORD")
    if not acceptance_password:
        raise SystemExit("Set CD80_ACCEPTANCE_PASSWORD for the disposable validation account.")
    report=json.loads((ROOT/'data/local-financial-verification.json').read_text(encoding='utf-8'))
    reviews=json.loads((ROOT/'data/local-financial-reviews.json').read_text(encoding='utf-8'))
    if not report.get('executionPassed'):raise RuntimeError('Execution evidence incomplete; review cannot enable or chat-test assets')
    app=create_app({'TESTING':True,'DATA_DIR':report['disposableDataDirectory'],'IMPORT_LEGACY_HISTORY':False,
        'CAPABILITY_SKIP_PUBLIC_SEED':True,'AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime')})
    wb=app.extensions['workbench_service'];lib=app.extensions['capability_library'];owner=report['owner']
    try:
        pending=[]
        for record in report['examples']:
            if record['kind']!='skill':continue
            review=reviews.get(record['manifestDigest'])
            if not review or review.get('decision')!='accept':
                pending.append(record['folder']);continue
            if record.get('origin')=='validation_suite':
                review=require_suite_review(record,review)
            verification=lib.store.get(owner,'verification',record['verificationId'])
            if verification['status']=='awaiting_review':
                summary={'decision':'accept','comment':review.get('comment') or '\n'.join(item['comment'] for item in review.get('cases',[]))}
                lib.review(owner,record['verificationId'],summary)
            asset=lib.asset(owner,record['assetId'])
            lib.update_asset(owner,record['assetId'],{'enabled':True,'activeVersionId':record['versionId']},'"'+str(asset['revision'])+'"')
            record['review']=review
            if record.get('origin')=='validation_suite':
                record['manualReviewRequired']=False
                for case,decision in zip(record['cases'],review['cases']):case['review']=decision
        report['manualReviewRequired']=bool(pending)
        report['validationPassed']=bool(report.get('executionPassed')) and not pending
        (ROOT/'data/local-financial-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if pending:
            print(json.dumps({'status':'blocked_review_required','packages':pending},ensure_ascii=False),flush=True)
            return 2
        client=app.test_client()
        assert client.post('/desktop/api/login',json={'email':'verify@example.invalid','password':acceptance_password}).status_code==200
        selected=next(e for e in report['examples'] if e['folder']=='cd80-history')
        for label,refs,message in [
            ('explicit',[{k:selected[k] for k in ('assetId','versionId')}],'请使用选中的本地历史日线查询600000.SH在2025-07-02的数据，列出收盘价、日期与来源。'),
            ('discovery',[],'请使用我个人库里的本地历史日线 Skill 查询600000.SH在2025-07-02的收盘价，找不到或不可用就停止。')]:
            response=client.post('/desktop/api/chat',json={'message':message,'capabilityRefs':refs})
            stream=response.data.decode('utf-8')
            (ROOT/f'data/local-financial-chat-{label}.txt').write_text(stream,encoding='utf-8')
            passed=response.status_code==200 and 'tool_completed' in stream and '14.2' in stream
            print(json.dumps({'chat':label,'passed':passed},ensure_ascii=False),flush=True)
            if not passed:raise RuntimeError(label+' main chat failed; inspect private SSE report')
    finally:wb.close()


if __name__=='__main__':raise SystemExit(main())
