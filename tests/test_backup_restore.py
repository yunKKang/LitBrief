"""Restore a real SQLite backup of synthetic editorial state and reproduce the site."""
from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from library import connect, ingest, review, build_issue, publish_issue, TOPICS
from webapp import export_site, validate_release
from test_workflow import PAPER


class RestoreTests(unittest.TestCase):
    def test_backup_restores_decisions_issues_holds_requests_and_identical_public_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with closing(connect(root/'source.sqlite3')) as db:
                ids=[]
                for name in ('published','draft','rejected','pending','review'):
                    with db:pid=ingest(db,{**PAPER,'doi':'10.1000/'+name,'urls':['https://example.org/'+name]},'fixture')
                    ids.append(pid)
                    if name in ('published','draft'):
                        review(db,pid,'approve','Fixture summary',[TOPICS[4]],'Fixture note',1)
                        issue=build_issue(db,date(2026,11,11) if name=='published' else date(2026,11,15))
                        if name=='published':publish_issue(db,issue)
                    elif name=='rejected':review(db,pid,'reject','',[],'irrelevant',1)
                    elif name=='review':
                        with db:db.execute("UPDATE papers SET state='awaiting_review' WHERE id=?",(pid,))
                with db:
                    db.execute("INSERT INTO enrichment_holds VALUES('openalex','rate_limited','2026-11-20T00:00:00+00:00')")
                    db.execute("INSERT INTO ai_requests VALUES('fixture','fingerprint','titles','[]','{}','fixture.json','billing_unknown',NULL,'{}','fixture','fixture')")
                public=export_site(db,root/'before')
                expected=validate_release(public)
                tables=['papers','identities','reviews','observations','issues','issue_items','runs','enrichment_holds','ai_requests']
                snapshots={t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in tables}
                with closing(sqlite3.connect(root/'backup.sqlite3')) as backup:
                    db.backup(backup)
            # Source connection is closed; recovery uses only the backup file.
            with closing(connect(root/'backup.sqlite3')) as restored:
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(restored.execute('PRAGMA foreign_key_check').fetchall(),[])
                for table in tables:
                    self.assertEqual(snapshots[table],[tuple(r) for r in restored.execute('SELECT * FROM '+table+' ORDER BY rowid')])
                release=export_site(restored,root/'after')
                self.assertEqual(expected,validate_release(release))
                self.assertFalse((release/'papers'/f'{ids[1]}.html').exists())
                self.assertFalse((release/'papers'/f'{ids[2]}.html').exists())

    def test_old_database_migration_restores_exclusion_without_approving_changed_material(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.sqlite3'
            with closing(connect(path)) as db:
                with db:pid=ingest(db,PAPER,'fixture')
                review(db,pid,'reject','',[],'old decision',1)
                with db:
                    db.execute("UPDATE papers SET state='pending'")
                    db.execute('ALTER TABLE papers DROP COLUMN human_decision')
                    db.execute('ALTER TABLE papers DROP COLUMN material_changed')
            with closing(connect(path)) as migrated:
                row=migrated.execute('SELECT * FROM papers').fetchone()
                self.assertEqual(row['state'],'rejected')
                self.assertEqual(row['human_decision'],'reject')
                self.assertEqual(row['material_changed'],1)


if __name__=='__main__':unittest.main()
