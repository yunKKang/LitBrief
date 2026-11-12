"""One serial, budget-gated scheduled cycle, with local static activation on Linux."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

from library import ROOT, DATA, connect, worker_lock, now
from automation import cycle


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=['weekly','monthly'])
    args=parser.parse_args()
    if (ROOT/'STAGING').exists():
        raise SystemExit('This is a validation copy; complete data cutover before scheduling paid work')
    cfg=json.loads((ROOT/'config/runtime.json').read_text('utf-8'))
    budget=cfg.get(args.kind+'_token_budget',0)
    if cfg.get('publication_mode')!='automatic' or type(budget) is not int or budget<=0:
        raise SystemExit('Scheduled paid publishing is disabled or its token budget is unset')
    with worker_lock(DATA),closing(connect()) as db:
        import sqlite3
        backup=DATA/'backups'/(now().replace(':','').replace('+','_')+'.sqlite3')
        backup.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(backup)) as dest:db.backup(dest)
        result=cycle(db,live=True,publish=True,max_tokens=budget,
                     max_groups=max(1,db.execute('SELECT count(*) FROM papers').fetchone()[0]),kind=args.kind)
        publication=result.get('publication') or {}
        if publication.get('public_directory'):
            from deploy_static import package
            sys.path.insert(0,str(ROOT/'deploy'))
            from activate_release import activate
            release,payload=package(ROOT/'outputs/public')
            result['deployment']=activate('/var/www/litbrief',release,payload)
            # A successful symlink switch is checked through the actual HTTPS host.
            import urllib.request,hashlib
            local=(Path(publication['public_directory'])/'index.html').read_bytes()
            with urllib.request.urlopen('https://litbrief.sustainable-systems.world/',timeout=30) as response:
                if hashlib.sha256(response.read()).digest()!=hashlib.sha256(local).digest():
                    raise RuntimeError('Public HTTPS content differs from the activated release')
        status=DATA/'last-scheduled-run.json'
        from webapp import atomic_write
        atomic_write(status,json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))
        if result['execution_status']!='completed':raise SystemExit(2)


if __name__=='__main__':main()
