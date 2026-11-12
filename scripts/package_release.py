"""Build a source-only deployment archive; secrets, runtime data and research evidence excluded."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / 'outputs/release'
    output.mkdir(parents=True, exist_ok=True)
    files = [ROOT / 'README.md', ROOT / '.env.example', ROOT / '.gitignore']
    for folder in ('scripts', 'config', 'docs', 'web', 'deploy', 'tests'):
        files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    manifest = {}
    with zipfile.ZipFile(output / 'literaturehot-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(files):
            relative = file.relative_to(ROOT).as_posix()
            if relative == '.env' or relative.startswith('outputs/'):
                raise ValueError('Private data cannot be packaged')
            archive.write(file, relative)
            manifest[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Created {output / "literaturehot-source.zip"}; {len(manifest)} source files, no runtime data')


if __name__ == '__main__':
    main()
