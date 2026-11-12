"""Generate an isolated synthetic review fixture; never touches the real library."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from library import ROOT, connect, ingest

folder = ROOT / 'outputs/ui-check'
folder.mkdir(parents=True, exist_ok=True)
with connect(folder / 'library.sqlite3') as db:
    ingest(db, {'journal_seed_issn': '2398-9629', 'title': 'Workflow test: housing retrofit',
                'doi': '10.1000/ui-test', 'abstract': 'Synthetic fixture: housing retrofit lowers demand.',
                'abstract_source': 'Synthetic UI test fixture'}, 'ui-test')
print('Synthetic fixture ready in outputs/ui-check only')
