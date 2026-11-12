"""Build self-hosted WenKai web subsets from licensed local desktop fonts.

Authoring-time only: fontTools + brotli. The web/server runtime has no font build dependency.
Include GB2312, common punctuation and current rendered Chinese text; rare new characters
fall back until the next font build. No article text is written to the font manifest.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from fontTools import subset
from fontTools.ttLib import TTFont

ROOT=Path(__file__).resolve().parents[1]

def characters():
    chars={chr(i) for i in range(32,256)}
    for start,end in [(0x2000,0x2070),(0x3000,0x3040),(0xff00,0xffef)]:
        chars.update(chr(i) for i in range(start,end))
    for a in range(0xa1,0xf8):
        for b in range(0xa1,0xff):
            try:chars.update(bytes([a,b]).decode('gb2312'))
            except UnicodeDecodeError:pass
    for folder in ('scripts','config'):
        for path in (ROOT/folder).glob('*'):
            if path.suffix in {'.py','.json'}:chars.update(path.read_text('utf-8-sig'))
    database=ROOT/'outputs/library/library.sqlite3'
    if database.exists():
        db=sqlite3.connect(f'file:{database.as_posix()}?mode=ro',uri=True)
        try:
            for data,result in db.execute('SELECT data,result FROM papers'):
                paper=json.loads(data);chars.update(paper.get('title',''))
                if result:
                    item=json.loads(result)
                    for key in ('title_zh','summary','reason'):chars.update(item.get(key,'') or '')
        finally:db.close()
    return {ord(c) for c in chars if ord(c)>=32}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font-dir',type=Path,default=Path(os.environ.get('LOCALAPPDATA',''))/'Microsoft/Windows/Fonts')
    args=parser.parse_args()
    target=ROOT/'web/fonts';target.mkdir(parents=True,exist_ok=True)
    codes=characters();rules=[];files=[]
    manifest_file=target/'manifest.json'
    previous=json.loads(manifest_file.read_text('utf-8')) if manifest_file.exists() else {}
    for style,weight in [('Regular',400),('Medium',500)]:
        font=TTFont(args.font_dir/f'LXGWWenKai-{style}.ttf',recalcTimestamp=False)
        options=subset.Options();options.flavor='woff2';options.recalc_timestamp=False
        options.name_IDs=['*'];options.name_legacy=True;options.name_languages=['*']
        sub=subset.Subsetter(options=options);sub.populate(unicodes=codes);sub.subset(font)
        font.flavor='woff2'
        temp=target/f'wenkai-{style.lower()}.tmp'
        font.save(temp);font.close()
        digest=hashlib.sha256(temp.read_bytes()).hexdigest()[:12]
        name=f'wenkai-{style.lower()}-{digest}.woff2';temp.replace(target/name);files.append(name)
        rules.append(f"@font-face{{font-family:'Reader WenKai';src:url('{name}') format('woff2');font-weight:{weight};font-style:normal;font-display:swap;}}")
    (target/'fonts.css').write_text('\n'.join(rules)+'\n',encoding='utf-8')
    manifest_file.write_text(json.dumps({'family':'LXGW WenKai','version':'1.522','requested_characters':len(codes),'files':files},indent=2),encoding='utf-8')
    for name in previous.get('files',[]):
        old=target/name
        if name not in files and old.resolve().parent==target.resolve() and old.name.startswith('wenkai-') and old.suffix=='.woff2':old.unlink(missing_ok=True)
    print(json.dumps({'files':files,'total_bytes':sum((target/name).stat().st_size for name in files),'characters':len(codes)},ensure_ascii=False))

if __name__=='__main__':main()
