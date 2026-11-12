"""Optional local Tabbit-assisted abstract capture, NOT a server collection guarantee."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit
from audit_sources import doi,pii_in,plain
from browser_collect import call,TASK
from library import ROOT,connect,ingest,now

def validate(snapshot,paper):
    expected_pii=next((pii_in(u) for u in paper.get('urls',[]) if pii_in(u)),None)
    if not expected_pii or snapshot.get('pii')!=expected_pii:raise ValueError('Publisher PII mismatch')
    identifier=doi(snapshot.get('doi'))
    if not identifier or (paper.get('doi') and paper['doi']!=identifier):raise ValueError('Publisher DOI mismatch')
    abstracts=list({plain(a) for a in snapshot.get('abstracts',[]) if plain(a)})
    if len(abstracts)!=1:raise ValueError('No unique formal author abstract')
    return {**paper,'doi':identifier,'abstract':abstracts[0],'abstract_source':'ScienceDirect publisher author abstract, local browser','online_date_source':'publisher_article',
            'abstract_url':snapshot['url'],'dates':{**paper.get('dates',{}),'publisher_online':snapshot.get('date','').replace('/','-')}}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ids',nargs='+',type=int,required=True);a=p.parse_args()
    folder=ROOT/'outputs/browser-abstracts';folder.mkdir(parents=True,exist_ok=True)
    with closing(connect()) as db:
        for pid in a.ids:
            row=db.execute('SELECT * FROM papers WHERE id=?',(pid,)).fetchone()
            if not row or row['state']!='awaiting_abstract':continue
            paper=json.loads(row['data']);result=json.loads(row['result'] or '{}')
            if not result.get('title_screened'):continue
            url=next((u for u in paper.get('urls',[]) if u.startswith('https://www.sciencedirect.com/science/article/pii/') and '/pdfft' not in u),None)
            if not url:continue
            program='await page.goto('+json.dumps(url)+",{waitUntil:'domcontentloaded'});\n"+'''
await page.waitForFunction(()=>document.querySelector('meta[name="citation_doi"]')||/are you a robot|verify you are human|access denied/i.test(document.body.innerText),null,{timeout:15000});
return await page.evaluate(()=>{const meta=n=>document.querySelector('meta[name="'+n+'"]')?.content||'';return {
 url:location.href,doi:meta('citation_doi'),pii:meta('citation_pii'),date:meta('citation_online_date'),title:meta('citation_title'),
 abstracts:[...document.querySelectorAll('#abstracts .abstract.author')].filter(e=>!e.querySelector('.abstract.author')&&/^(Abstract|Summary)$/i.test((e.querySelector('h2,h3')||e.parentElement.querySelector('h2,h3'))?.innerText.trim()||'')).map(e=>e.innerText.trim())};});
'''
            snapshot=call(program,'abstract-'+uuid.uuid4().hex)
            file=folder/(str(pid)+'.json');file.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
            try:enriched=validate(snapshot,paper)
            except ValueError as exc:
                with db:
                    db.execute("UPDATE papers SET enrichment_state='parser_failed' WHERE id=?",(pid,))
                    db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',(now(),'browser-abstract','needs_review',json.dumps({'paper_id':pid,'error':str(exc)})))
                print(pid,'needs review',flush=True);continue
            with db:
                actual=ingest(db,enriched,str(file));db.execute('UPDATE papers SET result=? WHERE id=?',(row['result'],actual))
                db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',(now(),'browser-abstract','available',json.dumps({'paper_id':pid,'source':url,'boundary':'local authenticated browser only'})))
            print(pid,'abstract available',flush=True);time.sleep(3)

if __name__=='__main__':main()
