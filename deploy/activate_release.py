"""Receive a public ZIP on stdin, verify it, and atomically switch LitBrief only.

Installed root-owned; executed by the site deployment user, without sudo.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import zipfile


PUBLIC_PATH = re.compile(r'(?:index\.html|archive\.html|about\.html|style\.css|tabler-icons-LICENSE\.txt|issues/\d{4}-\d{2}-\d{2}\.html|papers/\d+\.html|fonts/(?:[\w-]+\.woff2|fonts\.css|OFL\.txt))\Z')
RELEASE_ID = re.compile(r'[a-f0-9]{32}\Z')


def unpack(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or 'manifest.json' not in names:
            raise ValueError('Duplicate paths or missing manifest')
        if any(n != 'manifest.json' and not PUBLIC_PATH.fullmatch(n) for n in names):
            raise ValueError('Non-public path in archive')
        if sum(i.file_size for i in archive.infolist()) > 100 * 1024**2:
            raise ValueError('Release exceeds 100 MiB')
        manifest = json.loads(archive.read('manifest.json'))
        if set(manifest) != set(names) - {'manifest.json'}:
            raise ValueError('Manifest file set mismatch')
        required = {'index.html', 'archive.html', 'about.html', 'style.css', 'fonts/fonts.css', 'fonts/OFL.txt'}
        if not required <= set(manifest):
            raise ValueError('Incomplete public release')
        files = {n: archive.read(n) for n in manifest}
        if any(hashlib.sha256(body).hexdigest() != manifest[n] for n, body in files.items()):
            raise ValueError('Release checksum mismatch')
        return manifest, files


def verify(folder, manifest):
    paths = list(folder.rglob('*'))
    if folder.is_symlink() or any(p.is_symlink() for p in paths):
        raise ValueError('Release must not contain symlinks')
    actual = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths if p.is_file()}
    if actual != manifest:
        raise ValueError('Stored release differs from manifest')


def activate(base, release_id, data=None):
    import fcntl  # Linux server only; archive validation also runs on Windows.
    if not RELEASE_ID.fullmatch(release_id):
        raise ValueError('Invalid release ID')
    base = Path(base)
    if base.is_symlink() or not base.is_dir():
        raise ValueError('Site directory must already exist and not be a symlink')
    with (base / '.deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name in ('releases', 'manifests'):
            path = base / name
            if path.is_symlink():
                raise ValueError('Unsafe deployment directory')
            path.mkdir(exist_ok=True)
        release = base / 'releases' / release_id
        manifest_path = base / 'manifests' / (release_id + '.json')
        if data is not None:
            manifest, files = unpack(data)
            if release.exists():
                verify(release, manifest)  # Retry is safe only for identical content.
            else:
                release.mkdir(mode=0o755)
                for name, body in files.items():
                    dest = release / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(body)
                    dest.chmod(0o644)
                verify(release, manifest)
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
        else:
            manifest = json.loads(manifest_path.read_text('utf-8'))
            verify(release, manifest)
        current = base / 'current'
        if current.exists() and not current.is_symlink():
            raise ValueError('Current must be a symlink')
        previous = os.readlink(current) if current.is_symlink() else None
        tmp = base / '.current-next'
        if tmp.is_symlink():
            tmp.unlink()
        os.symlink('releases/' + release_id, tmp)
        os.replace(tmp, current)
        result = {'release': release_id, 'previous': previous, 'files': len(manifest)}
        with (base / 'deployments.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(result) + '\n')
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release_id')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    payload = None if args.rollback else sys.stdin.buffer.read(100 * 1024**2 + 1)
    if payload is not None and len(payload) > 100 * 1024**2:
        raise ValueError('Upload exceeds 100 MiB')
    print(json.dumps(activate('/var/www/litbrief', args.release_id, payload)))
