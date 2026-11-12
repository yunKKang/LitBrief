"""Upload only the latest verified public export over SSH. Never uploads the database."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from webapp import validate_release

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from activate_release import unpack


def package(container):
    container = Path(container).resolve()
    latest = json.loads((container / 'latest.json').read_text('utf-8'))
    relative = latest['directory']
    if not re.fullmatch(r'releases/[a-f0-9]{32}', relative):
        raise ValueError('Invalid latest release path')
    folder = container / relative
    if folder.is_symlink() or any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
                                  for p in [container, container / 'releases', folder, *folder.rglob('*')]):
        raise ValueError('Release must not contain links or junctions')
    manifest = validate_release(folder)
    if manifest != latest['files']:
        raise ValueError('Release changed after export; export again')
    # Archive the same bytes that were checked, including a private transport manifest.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, digest in manifest.items():
            body = (folder / name).read_bytes()
            if hashlib.sha256(body).hexdigest() != digest:
                raise ValueError('Release changed during packaging')
            archive.writestr(name, body)
        archive.writestr('manifest.json', json.dumps(manifest))
    payload = buffer.getvalue()
    unpack(payload)  # Reject private files before sending anything to the server.
    return folder.name, payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='SSH alias or user@host')
    parser.add_argument('--identity', type=Path)
    parser.add_argument('--public-output', type=Path, default=ROOT / 'outputs/public')
    parser.add_argument('--activate', action='store_true', help='Explicitly upload and switch production')
    parser.add_argument('--rollback', help='Switch to an existing verified release ID')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', args.host):
        parser.error('Invalid SSH host')
    if args.rollback:
        if not re.fullmatch(r'[a-f0-9]{32}', args.rollback):
            parser.error('Invalid rollback release ID')
        release_id, payload = args.rollback, None
    else:
        release_id, payload = package(args.public_output)
    if not args.activate:
        print(json.dumps({'release': release_id, 'upload_bytes': len(payload or b''), 'activated': False}))
        return
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15']
    if args.identity:
        command += ['-i', str(args.identity)]
    command += [args.host, 'python3 /usr/local/lib/litbrief/activate_release.py ' + release_id +
                (' --rollback' if args.rollback else '')]
    try:
        result = subprocess.run(command, input=payload or b'', capture_output=True, check=True, timeout=120)
    except subprocess.CalledProcessError as error:
        print(error.stderr.decode('utf-8', 'replace').strip(), file=sys.stderr)
        raise SystemExit('Deployment failed. Check remote current before retrying.') from None
    except subprocess.TimeoutExpired:
        raise SystemExit('SSH timed out; deployment outcome is unknown. Check remote current before retrying.') from None
    print(result.stdout.decode('utf-8').strip())


if __name__ == '__main__':
    main()
