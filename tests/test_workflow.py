import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import HTTPServer
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from library import connect, ingest, import_audit, review, build_issue, publish_issue, period, TOPICS, archive_inactive_sources
from webapp import export_site, make_handler, preview_window

PAPER = {'journal_seed_issn': '2398-9629', 'title': 'A housing transition study',
         'doi': '10.1000/example', 'urls': ['https://example.org/paper/a'],
         'abstract': 'PRIVATE_ABSTRACT: housing retrofit reduces energy demand.',
         'abstract_source': 'publisher verified', 'dates': {'published-online': '2026-11-10'}}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'library.sqlite3'
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add(self, paper=None):
        with self.db:
            return ingest(self.db, paper or PAPER, 'test')

    def approve(self, paper_id):
        row = self.db.execute('SELECT revision FROM papers WHERE id=?', (paper_id,)).fetchone()
        review(self.db, paper_id, 'approve', '住房改造降低能源需求。', [TOPICS[4]], 'checked', row[0])

    def test_publisher_only_record_merges_late_doi_without_losing_abstract(self):
        pid = self.add({**PAPER, 'doi': None})
        self.assertEqual(self.add({**PAPER, 'abstract': '', 'abstract_source': None}), pid)
        self.assertEqual(self.db.execute('SELECT count(*) FROM papers').fetchone()[0], 1)
        data = json.loads(self.db.execute('SELECT data FROM papers').fetchone()[0])
        self.assertEqual(data['doi'], PAPER['doi'])
        self.assertEqual(data['abstract'], PAPER['abstract'])

    def test_title_similarity_is_not_identity(self):
        self.add()
        self.add({**PAPER, 'doi': '10.1000/another', 'urls': ['https://example.org/another']})
        self.assertEqual(self.db.execute('SELECT count(*) FROM papers').fetchone()[0], 2)

    def test_crossref_does_not_reset_publisher_type_and_review(self):
        pid = self.add({**PAPER, 'title_source':'publisher_catalogue', 'type':'Article'})
        self.approve(pid)
        self.add({**PAPER, 'title':'A differently punctuated Crossref title', 'title_source':'Crossref', 'type':'journal-article'})
        row=self.db.execute('SELECT state,data FROM papers WHERE id=?',(pid,)).fetchone()
        self.assertEqual(row['state'],'approved')
        data=json.loads(row['data'])
        self.assertEqual(data['type'],'Article')
        self.assertEqual(data['title'],PAPER['title'])

    def test_missing_material_cannot_be_approved(self):
        pid = self.add({**PAPER, 'abstract_source': None})
        with self.assertRaises(ValueError):
            self.approve(pid)
        self.assertEqual(self.db.execute('SELECT state FROM papers').fetchone()[0], 'awaiting_abstract')

    def test_concurrent_review_rejects_stale_revision(self):
        pid = self.add()
        self.approve(pid)
        with self.assertRaises(ValueError):
            review(self.db, pid, 'reject', '', [], '', 1)

    def test_archive_snapshot_and_public_allowlist(self):
        pid = self.add()
        self.approve(pid)
        issue = build_issue(self.db, date(2026, 11, 11))
        output = self.root / 'public'
        output = export_site(self.db, self.root / 'public')
        self.assertFalse((output / 'papers' / f'{pid}.html').exists())
        font_css=(output/'fonts/fonts.css').read_text(encoding='utf-8')
        self.assertIn('Reader WenKai',font_css)
        self.assertTrue((output/'fonts/OFL.txt').exists())
        self.assertEqual(len(list((output/'fonts').glob('*.woff2'))),2)
        publish_issue(self.db, issue)
        self.assertEqual(build_issue(self.db, date(2026,11,12)), issue)
        output = export_site(self.db, self.root / 'public')
        article = (output / 'papers' / f'{pid}.html').read_text(encoding='utf-8')
        self.assertIn('住房改造', article)
        self.assertNotIn('PRIVATE_ABSTRACT', article)
        self.assertNotIn('checked', article)
        self.add({**PAPER, 'abstract': 'A changed publisher abstract.'})
        output = export_site(self.db, self.root / 'public')
        self.assertEqual(article, (output / 'papers' / f'{pid}.html').read_text(encoding='utf-8'))
        self.assertEqual(build_issue(self.db, date(2026,11,15)), '2026-11-15')
        self.assertEqual(self.db.execute('SELECT count(*) FROM issue_items').fetchone()[0], 1)
        with self.assertRaises(ValueError):
            publish_issue(self.db, '2026-11-15')

    def test_changed_abstract_removes_draft_item_and_requires_review(self):
        pid = self.add()
        self.approve(pid)
        build_issue(self.db, date(2026,11,11))
        self.add({**PAPER, 'abstract': 'Updated abstract from publisher.'})
        self.assertEqual(self.db.execute('SELECT count(*) FROM issue_items').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT state FROM papers').fetchone()[0], 'needs_review')

    def test_material_update_preserves_human_rejection(self):
        pid = self.add()
        review(self.db, pid, 'reject', '', [], 'Not relevant', 1)
        self.add({**PAPER, 'abstract': 'Updated publisher evidence.'})
        row = self.db.execute('SELECT * FROM papers WHERE id=?', (pid,)).fetchone()
        self.assertEqual(row['state'], 'rejected')
        self.assertEqual(row['human_decision'], 'reject')
        self.assertEqual(row['material_changed'], 1)
        review(self.db, pid, 'requeue', '', [], 'Explicit reconsideration', row['revision'])
        row = self.db.execute('SELECT * FROM papers WHERE id=?', (pid,)).fetchone()
        self.assertEqual(row['state'], 'pending')
        self.assertEqual(row['human_decision'], '')

    def test_export_failure_preserves_previous_complete_release(self):
        from unittest.mock import patch
        import webapp
        root = self.root / 'public'
        old = export_site(self.db, root)
        old_files = {p.relative_to(old): p.read_bytes() for p in old.rglob('*') if p.is_file()}
        pointer = (root / 'latest.json').read_bytes()
        original = webapp.atomic_write
        def fail(path, text):
            if path.name == 'archive.html':
                raise OSError('Simulated interruption')
            original(path, text)
        with patch.object(webapp, 'atomic_write', side_effect=fail):
            with self.assertRaises(OSError):
                export_site(self.db, root)
        self.assertEqual(pointer, (root / 'latest.json').read_bytes())
        self.assertEqual(old_files, {p.relative_to(old): p.read_bytes() for p in old.rglob('*') if p.is_file()})
        latest = export_site(self.db, root)
        self.assertNotEqual(old, latest)
        self.assertTrue((latest / 'index.html').exists())

    def test_four_day_period_crosses_month_and_year(self):
        self.assertEqual(period(date(2026,11,14)), ('2026-11-11','2026-11-14'))
        self.assertEqual(period(date(2026,11,15)), ('2026-11-15','2026-11-18'))
        start,end = period(date(2027,1,1))
        self.assertEqual((date.fromisoformat(end)-date.fromisoformat(start)).days,3)

    def test_preview_window_tracks_collection_not_wall_clock(self):
        with self.db:
            self.db.execute('INSERT INTO runs(at,kind,status,detail) VALUES(?,?,?,?)',
                            ('2026-11-11T13:00:00+00:00','collect','partial',json.dumps({'end':'2026-11-11'})))
        self.assertEqual(preview_window(self.db),('2026-11-08','2026-11-11'))

    def test_publisher_pending_import_without_crossref(self):
        folder = self.root / 'audit'
        folder.mkdir()
        (folder/'publisher-pending.json').write_text(json.dumps([{**PAPER, 'doi': None, 'abstract': ''}]))
        self.assertEqual(import_audit(self.db, folder),1)
        self.assertEqual(self.db.execute('SELECT state FROM papers').fetchone()[0],'awaiting_abstract')

    def test_html_escaping_and_unsafe_link(self):
        pid = self.add({**PAPER, 'title': '<script>alert(1)</script>', 'urls': ['javascript:alert(1)']})
        self.approve(pid)
        issue = build_issue(self.db, date(2026,11,11))
        publish_issue(self.db,issue)
        release = export_site(self.db,self.root/'public')
        page = (release/'papers'/f'{pid}.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>alert',page)
        self.assertNotIn('javascript:',page)

    def test_export_rejects_directory_containing_private_files(self):
        private = self.root / 'unsafe-export'
        private.mkdir()
        (private / '.env').write_text('not-a-real-secret')
        with self.assertRaises(ValueError):
            export_site(self.db, private)

    def test_public_release_never_contains_private_workbench_navigation(self):
        pid=self.add();self.approve(pid)
        issue=build_issue(self.db,date(2026,11,11));publish_issue(self.db,issue)
        release=export_site(self.db,self.root/'public')
        for page in release.rglob('*.html'):
            text=page.read_text('utf-8')
            for private in ('管理与运行','href="/retention"','href="/queue"','action="/feedback"','技术运行日志'):
                self.assertNotIn(private,text)

    def test_source_retirement_waits_for_in_flight_request(self):
        from unittest.mock import patch
        pid = self.add()
        with self.db:
            self.db.execute("UPDATE papers SET state='screening' WHERE id=?", (pid,))
        with patch('library.registry', return_value={}):
            with self.assertRaises(ValueError):
                archive_inactive_sources(self.db)
        self.assertNotEqual(self.db.execute('SELECT cohort FROM papers WHERE id=?', (pid,)).fetchone()[0], 'inactive_source')

    def test_review_http_host_csrf_and_private_files(self):
        server = HTTPServer(('127.0.0.1',0), make_handler(self.path,'test-csrf',self.root/'public'))
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base) as res:
                self.assertEqual(res.status,200)
            with urlopen(base+'/fonts/fonts.css') as res:
                self.assertIn('text/css',res.headers['Content-Type'])
                self.assertIn(b'Reader WenKai',res.read())
            for url, headers, data, expected in [
                (base+'/.env',{},None,404),
                (base+'/fonts/../../.env',{},None,404),
                (base,{'Host':'attacker.example'},None,403),
                (base+'/prepare',{},b'csrf=bad',403),
                (base+'/prepare',{'Origin':'https://attacker.example'},b'csrf=test-csrf',403),
            ]:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url,headers=headers,data=data))
                self.assertEqual(caught.exception.code,expected)
                caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
