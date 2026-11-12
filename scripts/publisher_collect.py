"""Independent publisher HTML collector. No Codex, Tabbit, model or logged-in session required.

This certifies only the visible publisher pages it successfully parses. Access challenges
and unrecognised HTML are explicit failures, never interpreted as an empty journal.
"""
from contextlib import closing
from datetime import datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import time
from urllib.parse import urljoin

from audit_sources import HTTP, plain, save
from library import ROOT, DATA, connect, registry, now, ingest

class ListingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items=[];self.links=[];self.card=None;self.depth=0;self.anchor=None;self.capture=[];self.text=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);classes=a.get('class','').split()
        if tag=='dl' and 'js-article' in classes and self.card is None:
            self.card={'title':'','type':'','date_text':'','url':''};self.depth=1
        elif tag=='dl' and self.card is not None:self.depth+=1
        if tag=='a':
            self.anchor={'url':a.get('href',''),'text':[]}
            if self.card is not None and 'article-content-title' in classes:
                self.card['url']=a.get('href','');self.capture.append(('title',tag))
        if self.card is not None:
            if 'js-article-subtype' in classes:self.capture.append(('type',tag))
            if 'js-article-item-aip-date' in classes:self.capture.append(('date_text',tag))
    def handle_data(self,data):
        self.text.append(data)
        if self.anchor is not None:self.anchor['text'].append(data)
        if self.card is not None and self.capture:self.card[self.capture[-1][0]]+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.anchor is not None:
            self.links.append({'url':self.anchor['url'],'text':plain(''.join(self.anchor['text']))});self.anchor=None
        if self.capture and self.capture[-1][1]==tag:self.capture.pop()
        if tag=='dl' and self.card is not None:
            self.depth-=1
            if self.depth==0:
                self.items.append({k:plain(v) for k,v in self.card.items()});self.card=None;self.capture=[]

def collect(db,folder,only=None):
    http=HTTP(folder);report=[]
    for j in registry().values():
        if j['publisher_family']!='ScienceDirect' or (only and j['seed_issn'] not in only):continue
        record={'journal':j['title'],'seed_issn':j['seed_issn'],'pages':[],'status':'unverified'}
        meta,body=http.get(j['homepage'],'text/html')
        home=ListingParser();home.feed(body.decode('utf-8',errors='replace'))
        links=list(dict.fromkeys(urljoin(j['homepage'],a['url']) for a in home.links if a['text'].lower() in {'latest issue','articles in press'}))
        links=[u for u in links if u.startswith(j['homepage']+'/')]
        if not meta['ok'] or not links:
            record.update(status='access_blocked' if meta.get('status') in {401,403,429} or any(s in ''.join(home.text).lower() for s in ['are you a robot','verify you are human']) else 'unrecognised_directory',http_status=meta.get('status'))
        for url in links:
            time.sleep(2)
            meta,body=http.get(url,'text/html');parser=ListingParser();parser.feed(body.decode('utf-8',errors='replace'))
            status='parsed' if meta['ok'] and parser.items else 'access_or_parser_failure'
            count=0
            with db:
                if status=='parsed':
                    for item in parser.items:
                        article_url=urljoin(url,item['url'])
                        if not article_url.startswith('https://www.sciencedirect.com/science/article/pii/'):continue
                        dates={}
                        if 'Available online ' in item['date_text']:
                            try:dates['publisher_online']=datetime.strptime(item['date_text'].split('Available online ')[-1],'%d %B %Y').date().isoformat()
                            except ValueError:pass
                        ingest(db,{'journal_seed_issn':j['seed_issn'],'title':item['title'],'type':item['type'],
                                   'title_source':'publisher_catalogue','urls':[article_url],'dates':dates},url)
                        count+=1
            record['pages'].append({'url':url,'status':status,'items':count,'http_status':meta.get('status')})
        if record['pages']:record['status']='partial' # No full-window completeness claim.
        save(Path(folder)/(j['seed_issn']+'.json'),record)
        with db:db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',(now(),'publisher-server-http',record['status'],json.dumps(record,ensure_ascii=False)))
        report.append(record)
        print(j['title'],record['status'],flush=True)
        time.sleep(2)
    return report

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--only',nargs='+');a=p.parse_args()
    with closing(connect()) as db:collect(db,DATA/'publisher-http'/now().replace(':','').replace('+','_'),a.only)
