"""Per-journal discovery evidence. Successful HTTP never certifies a complete window."""
import json
from pathlib import Path

from library import registry, now


def summarize(db, folder, start, end, only=None):
    folder=Path(folder)
    known={r[0][4:] for r in db.execute("SELECT identity FROM identities WHERE identity LIKE 'doi:%'")}
    rows=[]
    for seed,source in registry().items():
        if only is not None and seed not in only:continue
        item={'journal':source['title'],'issn':seed,'start':start,'end':end,'checked_at':now(),
              'coverage':'unknown','feed_items':0,'feeds':[],'catalogue_items':0,'missing_dois':[],
              'crossref_complete':False}
        feed=folder/'publisher'/(seed+'.json')
        if feed.is_file():
            for entry in json.loads(feed.read_text('utf-8')).get('sources',[]):
                req=entry.get('request',{});parsed=entry.get('parsed')
                status=('parsed' if parsed else 'rate_limited' if req.get('status')==429 else
                        'access_blocked' if req.get('status') in (401,403) else
                        'unrecognized_content' if req.get('ok') else 'request_failed')
                count=len((parsed or {}).get('items',[]))
                item['feeds'].append({'url':entry.get('url'),'status':status,'http_status':req.get('status'),'items':count})
                item['feed_items']+=count
                if parsed and count:item['coverage']='partial'
        catalogues=[]
        for year in range(int(start[:4]),int(end[:4])+1):
            path=folder/str(year)/'catalogue'/(seed+'.json')
            if path.is_file():catalogues.append(json.loads(path.read_text('utf-8')))
        expected={r['doi'] for c in catalogues for r in c.get('items',[]) if r.get('doi')}
        item['catalogue_items']=len(expected);item['missing_dois']=sorted(expected-known)
        if len(catalogues)==int(end[:4])-int(start[:4])+1 and all(c.get('range_traversed') for c in catalogues) and not item['missing_dois']:
            item['coverage']='catalogue_checked'
        elif expected:item['coverage']='partial'
        item['catalogue_errors']=[c.get('error') for c in catalogues if c.get('error')]
        cr=folder/'journals'/(seed+'.json')
        if cr.is_file():
            value=json.loads(cr.read_text('utf-8'));item['crossref_complete']=bool(value.get('crossref_complete'))
            if value.get('windows') and item['coverage']=='unknown':item['coverage']='partial'
        rows.append(item)
    return rows
