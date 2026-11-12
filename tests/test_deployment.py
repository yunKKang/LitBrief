"""No network: validate the public upload boundary and transport integrity."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deploy_static import package, unpack


class DeploymentTests(unittest.TestCase):
    def fixture(self, root):
        folder = root / 'releases' / ('a' * 32)
        folder.mkdir(parents=True)
        files = {'index.html': b'<a href="archive.html">Archive</a>', 'archive.html': b'archive',
                 'about.html': b'about', 'style.css': b'css', 'fonts/fonts.css': b'css',
                 'fonts/OFL.txt': b'license'}
        for name, body in files.items():
            path = folder / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(body)
        manifest = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
        (root / 'latest.json').write_text(json.dumps({'directory': 'releases/' + folder.name, 'files': manifest}))
        return folder, manifest

    def test_package_roundtrip_and_modified_export_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder, expected = self.fixture(root)
            release_id, data = package(root)
            manifest, files = unpack(data)
            self.assertEqual(release_id, 'a' * 32)
            self.assertEqual(manifest, expected)
            self.assertEqual(files['about.html'], b'about')
            (folder / 'about.html').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'changed after export'):
                package(root)

    def test_private_path_rejected_even_if_present_in_manifest(self):
        for name in ('.env', '../index.html', 'papers/private.json', 'fonts/key.pem'):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr(name, b'private')
                archive.writestr('manifest.json', json.dumps({name: hashlib.sha256(b'private').hexdigest()}))
            with self.assertRaisesRegex(ValueError, 'Non-public'):
                unpack(buffer.getvalue())

    def test_corrupt_upload_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            _, data = package(root)
            buffer = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(buffer, 'w') as altered:
                for name in original.namelist():
                    altered.writestr(name, b'changed' if name == 'about.html' else original.read(name))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                unpack(buffer.getvalue())


if __name__ == '__main__':
    unittest.main()
