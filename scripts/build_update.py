"""Build a portable update ZIP without user data or machine-specific configuration."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()
FILES = ['app.py', 'requirements.txt', 'start.ps1', 'Start Oranje.cmd', 'installeren.ps1', 'Installeren.cmd', 'README.md', 'VALIDATIE.md', 'VERSION', 'Update ophalen.cmd']
FILES += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'oranje').glob('*.py')) if p.name != 'seed.py']
FILES += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'static').iterdir()) if p.is_file()]

def build(destination=None):
    destination = Path(destination) if destination else ROOT / 'updates' / f'Oranje-update-{VERSION}.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'app':'oranje-maaltijden', 'version':VERSION, 'files':[{'path':name,'sha256':hashlib.sha256((ROOT/name).read_bytes()).hexdigest()} for name in FILES]}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT/'update').iterdir()):
            if path.is_file(): archive.writestr(path.name, path.read_bytes())
        archive.writestr('manifest.json', json.dumps(manifest, indent=2))
        for name in FILES: archive.writestr('payload/'+name, (ROOT/name).read_bytes())
    return destination

if __name__ == '__main__': print(build())
