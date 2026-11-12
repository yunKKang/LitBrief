"""Small private build identity; never includes environment values or raw evidence."""
import hashlib
import json
from pathlib import Path
import subprocess

from library import ROOT, now


def build_identity(db):
    commit=None
    try:
        commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,check=True,capture_output=True,text=True,timeout=5).stdout.strip()
    except (OSError,subprocess.SubprocessError):
        source=ROOT/'SOURCE.json'
        if source.is_file():commit=json.loads(source.read_text('utf-8')).get('commit_sha')
    files={}
    for name in ('scripts','config','web','deploy'):
        for path in sorted((ROOT/name).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
                files[path.relative_to(ROOT).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    rule=ROOT/'docs/journal-selection.md'
    files['docs/journal-selection.md']=hashlib.sha256(rule.read_bytes()).hexdigest()
    snapshots=[list(r) for r in db.execute("SELECT issue_id,paper_id,snapshot FROM issue_items JOIN issues ON issues.id=issue_id WHERE state='published' ORDER BY issue_id,paper_id")]
    return {'commit_sha':commit,'built_at':now(),
            'source_tree_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
            'runtime_config_sha256':files['config/runtime.json'],
            'selection_rules_sha256':files['docs/journal-selection.md'],
            'snapshot_sha256':hashlib.sha256(json.dumps(snapshots,ensure_ascii=False).encode()).hexdigest(),
            'article_count':len(snapshots)}
