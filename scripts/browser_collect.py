"""Collect visible ScienceDirect publisher listings through the user's Tabbit session.

Uses the installed supported CLI, never copies cookies or institution credentials.
An access challenge is logged and left for the user, never bypassed.
"""
import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from library import ROOT, connect, ingest, now, registry

TASK='LiteratureHOT publisher capture'


def call(program, request_id):
    launcher=Path(os.environ['LOCALAPPDATA'])/'Tabbit/LocalAgent/bin/tabbit-cli.exe'
    proc=subprocess.run([str(launcher),'nodejs','--task',TASK,'--request-id',request_id,'--timeout-ms','60000'],input=program,text=True,encoding='utf-8',capture_output=True,timeout=75)
    result=json.loads(proc.stdout)
    log=ROOT/'outputs/browser-capture/receipts'
    log.mkdir(parents=True,exist_ok=True)
    (log/(request_id+'.json')).write_text(proc.stdout,encoding='utf-8')
    if result.get('status')!='succeeded':
        raise RuntimeError('Browser program failed; inspect the retained publisher tab')
    if 'value' in result['result']:
        return result['result']['value']
    resource=result['result']['resourceId']
    chunks=[];offset=0
    while True:
        part=subprocess.run([str(launcher),'resource','--task',TASK,'--resource',resource,'--offset',str(offset),'--max-bytes','65536'],capture_output=True,text=True,encoding='utf-8',timeout=20)
        parsed=json.loads(part.stdout)
        chunks.append(parsed['data'])
        if parsed['eof']:break
        offset=parsed['nextOffset']
    return json.loads(''.join(chunks))


def list_program(journal):
    return 'const journal='+json.dumps(journal)+';\n'+'''
await page.goto(journal.homepage,{waitUntil:'domcontentloaded'});
await page.waitForFunction(()=>[...document.querySelectorAll('a')].some(a=>/^(latest issue|articles in press)$/i.test(a.innerText.trim()))||/are you a robot|verify you are human|access denied/i.test(document.body.innerText),null,{timeout:15000});
const destinations=await page.evaluate(()=>[...document.querySelectorAll('a')].filter(a=>/^(latest issue|articles in press)$/i.test(a.innerText.trim())).map(a=>a.href));
const unique=[...new Set(destinations)].filter(u=>u.startsWith(journal.homepage+'/'));
const snapshots=[];
for(const url of unique.slice(0,3)){
 await page.goto(url,{waitUntil:'domcontentloaded'});
 let ready=true;
 try {await page.waitForFunction(()=>document.querySelectorAll('dl.js-article').length>0||/are you a robot|verify you are human|access denied|no articles/i.test(document.body.innerText),null,{timeout:15000});} catch(e){ready=false;}
 const snapshot=await page.evaluate(()=>{
  const cards=[...document.querySelectorAll('dl.js-article')];
  return {url:location.href,title:document.title,items:cards.map(card=>{const a=card.querySelector('a.article-content-title');return {
   title:a?.innerText||'',url:a?.href||'',type:card.querySelector('.js-article-subtype')?.innerText||'',
   doi:card.closest('li')?.querySelector('div[hidden]')?.textContent?.trim()||null,
   online_date_text:card.querySelector('.js-article-item-aip-date')?.innerText||'',
   }}),
   pagination:[...document.querySelectorAll('a,button')].filter(a=>/^(next|next page)$/i.test(a.innerText.trim())).map(a=>({text:a.innerText,url:a.href||null})),
   blocked:/are you a robot|verify you are human|access denied/i.test(document.body.innerText)};
 });
 snapshot.ready=ready;
 snapshots.push(snapshot);
}
return {journal:journal.title,seed_issn:journal.seed_issn,homepage:journal.homepage,snapshots,discovered_links:unique};
'''


def import_snapshot(db, result, origin):
    seed=result['seed_issn']
    if seed not in registry():raise ValueError('Unknown journal')
    home=registry()[seed]['homepage']
    count=0
    with db:
        for page in result['snapshots']:
            if page.get('blocked') or not page['url'].startswith(home+'/'):continue
            for item in page['items']:
                if not item['url'].startswith('https://www.sciencedirect.com/science/article/pii/'):continue
                dates={}
                raw=item.get('online_date_text','').split('Available online ')[-1]
                try:dates['publisher_online']=datetime.strptime(raw.strip(),'%d %B %Y').date().isoformat()
                except ValueError:pass
                ingest(db,{'title':item['title'],'doi':item.get('doi'),'type':item['type'],'title_source':'publisher_catalogue',
                           'journal_seed_issn':seed,'urls':[item['url']],'dates':dates,
                           'browser_listing_url':page['url']},origin)
                count+=1
        db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',(now(),'publisher-browser','partial',json.dumps({
            'journal':result['journal'],'items':count,'pages':len(result['snapshots']),
            'blocked':any(p.get('blocked') for p in result['snapshots']),
            'coverage':'visible latest issue and articles-in-press pages; pagination and full-window coverage not certified'})))
    return count


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--only',nargs='+')
    args=p.parse_args()
    folder=ROOT/'outputs/browser-capture'/now().replace(':','').replace('+','_')
    folder.mkdir(parents=True,exist_ok=True)
    with closing(connect()) as db:
        for j in registry().values():
            if j['publisher_family']!='ScienceDirect' or (args.only and j['seed_issn'] not in args.only):continue
            result=call(list_program(j),'listing-'+uuid.uuid4().hex)
            file=folder/(j['seed_issn']+'.json')
            file.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            count=import_snapshot(db,result,str(file))
            print(j['title'],count,'publisher records',flush=True)
            time.sleep(3)
    launcher=Path(os.environ['LOCALAPPDATA'])/'Tabbit/LocalAgent/bin/tabbit-cli.exe'
    subprocess.run([str(launcher),'finish','--task',TASK],capture_output=True,timeout=20)


if __name__=='__main__':main()
