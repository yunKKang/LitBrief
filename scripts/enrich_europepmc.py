"""Public abstract supplement, used only after title triage and exact DOI verification."""
from contextlib import closing
import json
import time
from urllib.parse import urlencode
from audit_sources import HTTP,doi,plain
from library import DATA,connect,ingest,now,worker_lock

def extract(payload,identifier):
    matches=[r for r in payload.get('resultList',{}).get('result',[]) if doi(r.get('doi'))==doi(identifier)]
    abstracts={plain(r.get('abstractText')) for r in matches if r.get('abstractText')}
    return next(iter(abstracts)) if len(abstracts)==1 else None

def run(db):
    with worker_lock(DATA):
        http=HTTP(DATA/'europepmc'/now().replace(':','').replace('+','_'))
        added=attempted=0
        for r in db.execute("SELECT * FROM papers WHERE cohort='monitoring' AND state='awaiting_abstract'").fetchall():
            paper=json.loads(r['data']);result=json.loads(r['result'] or '{}')
            if not paper.get('doi') or not result.get('title_screened'):continue
            # Avoid repeatedly requesting an already-attempted identical DOI.
            marker='europepmc:'+paper['doi']
            if db.execute("SELECT 1 FROM runs WHERE kind='abstract-europepmc' AND detail LIKE ?",('%'+marker+'%',)).fetchone():continue
            url='https://www.ebi.ac.uk/europepmc/webservices/rest/search?'+urlencode({'query':'DOI:"'+paper['doi']+'"','format':'json','resultType':'core'})
            meta,body=http.get(url,'application/json');abstract=None;attempted+=1
            if meta['ok']:
                try:abstract=extract(json.loads(body),paper['doi'])
                except (ValueError,TypeError):pass
            status='available' if abstract else 'not_available' if meta['ok'] else 'access_blocked' if meta.get('status') in {401,403,429} else 'request_failed'
            with db:
                if abstract:
                    pid=ingest(db,{**paper,'abstract':abstract,'abstract_source':'Europe PMC, exact DOI verified','abstract_url':url},str(http.output))
                    db.execute('UPDATE papers SET result=? WHERE id=?',(r['result'],pid));added+=1
                db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',(now(),'abstract-europepmc',status,json.dumps({'marker':marker,'paper_id':r['id'],'http_status':meta.get('status')})))
            print(r['id'],status,flush=True);time.sleep(3)
        return {'attempted':attempted,'abstracts_added':added}

if __name__=='__main__':
    with closing(connect()) as db:print(run(db))
