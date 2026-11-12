"""Conservative, auditable article-type exclusions. Never exclude by vague topic keywords."""
import argparse
from contextlib import closing
import json
import re
from library import connect, now

EXCLUDED_TYPES={'news','news & views','news feature','news in focus','editorial','correction','author correction',
 'publisher correction','erratum','corrigendum','retraction','retraction note','book review','obituary',
 'career column','careers','research highlight','research highlights','advertisement'}
TITLE_PREFIX=re.compile(r'^(author correction|publisher correction|correction|corrigendum|erratum|retraction(?: note)?|retracted)\s*[:：]',re.I)

def exclusion(paper):
    kind=str(paper.get('type') or paper.get('article_type') or '').strip().lower()
    if kind in EXCLUDED_TYPES:return f'出版社文献类型为 {kind}，不作为新研究推荐。'
    if TITLE_PREFIX.match(paper.get('title','')):return '标题明确标识为更正或撤稿通知，保留记录供原文更新核对。'
    return None

def run(db,cohort='monitoring'):
    count=0
    with db:
        rows=db.execute("SELECT * FROM papers WHERE cohort=? AND state IN ('pending','awaiting_abstract')",(cohort,)).fetchall()
        for r in rows:
            reason=exclusion(json.loads(r['data']))
            if not reason:continue
            result={'decision':'exclude','reason':reason,'basis':'出版社类型／明确通知标题','summary':'','evidence':[], 'prefilter':'article-type-v1'}
            db.execute("UPDATE papers SET state='type_excluded',result=?,revision=revision+1,updated_at=? WHERE id=?",(json.dumps(result,ensure_ascii=False),now(),r['id']))
            count+=1
        db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',(now(),'type-prefilter','completed',json.dumps({'cohort':cohort,'excluded':count,'paid_calls':0})))
    return count

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cohort',choices=['monitoring','baseline','date_unverified'],default='monitoring');a=p.parse_args()
    with closing(connect()) as db:print('Type exclusions:',run(db,a.cohort))
