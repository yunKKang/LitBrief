"""Public abstract supplement, used only after title triage and exact DOI verification."""
from contextlib import closing
import json
from audit_sources import doi
from library import connect

def extract(payload,identifier):
    from enrichment import parse_material
    result = parse_material('europepmc', json.dumps(payload).encode(), doi(identifier))
    return result.get('abstract') if result['status'] == 'available' else None

def run(db):
    # Legacy CLI alias: one shared queue owns retries and provenance now.
    from enrichment import run as enrich
    return enrich(db, max_items=20)

if __name__=='__main__':
    with closing(connect()) as db:print(run(db))
