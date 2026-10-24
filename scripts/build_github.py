"""Prepare a public-safe repository tree and a one-file Windows GitHub updater."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPO = 'FirstKraw/OR-automatisering'
RAW = 'https://raw.githubusercontent.com/' + REPO + '/main/'

def launcher():
    source = (ROOT/'update/github-bootstrap.ps1').read_text(encoding='utf-8')
    command = "$s=[IO.File]::ReadAllText($env:ORANJE_UPDATE_SELF); $m=[char]10+'# ORANJE_POWERSHELL'; $i=$s.IndexOf($m); if($i -lt 0){exit 1}; & ([ScriptBlock]::Create($s.Substring($i+$m.Length)))"
    return '@echo off\r\nset "ORANJE_UPDATE_DIR=%~dp0"\r\nset "ORANJE_UPDATE_SELF=%~f0"\r\npowershell.exe -NoProfile -STA -ExecutionPolicy Bypass -Command "' + command + '"\r\nexit /b %errorlevel%\r\n# ORANJE_POWERSHELL\r\n' + source.replace('\n','\r\n')

def build():
    spec=importlib.util.spec_from_file_location('package',ROOT/'scripts/build_update.py')
    package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
    (ROOT/'Update ophalen.cmd').write_bytes(launcher().encode('ascii'))
    version=(ROOT/'VERSION').read_text().strip()
    destination=ROOT/'github-publicatie'
    destination.mkdir(exist_ok=True)
    files = package.FILES + ['scripts/build_update.py','scripts/build_github.py','update/Update.ps1','update/Update uitvoeren.cmd','update/LEESMIJ.txt','update/github-bootstrap.ps1','Update ophalen.cmd']
    for name in files:
        if name in ('README.md','VALIDATIE.md'):continue
        path=destination/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,path)
    # Never publish the original contact seed. Existing installed copies retain theirs.
    (destination/'oranje/seed.py').write_text('"""Nieuwe installaties beginnen zonder persoonsgegevens."""\nCONTACTS = []\n',encoding='utf-8')
    (destination/'.gitignore').write_text('data/\n.venv/\n__pycache__/\n*.pyc\nruntime.json\n*.lnk\nupdate-backups/\ngithub-update.log\ntmp/\n',encoding='utf-8')
    (destination/'README.md').write_text('''# OR-automatisering
bestellingen beter en rapper ingeven

Nederlandstalig lokaal maaltijdenbeheer. De administratie blijft op de eigen pc.

## Een bestaande installatie bijwerken

1. Download **Update ophalen.cmd** uit deze repository (Download raw file).
2. Sluit de app volledig, ook het zwarte startvenster.
3. Zet het bestand in de bestaande appmap, naast Start Oranje.cmd, en dubbelklik.
4. Het bestand haalt de nieuwste update van deze repository op en controleert de download.
5. De installatie maakt een reservekopie en behoudt de administratie, contacten en instellingen.
6. Start opnieuw en vernieuw de browser. Maak bestaande maandafrekeningen opnieuw voor de nieuwe PDF-kop en leesbare code.

Er is geen Git-installatie of GitHub-account nodig op de gebruikers-pc. Werkt iets niet, bekijk github-update.log in de appmap.

## Nieuwe installatie

Download de broncode als ZIP, pak uit en installeer Python 3.10 of nieuwer met Python in PATH. Voer Installeren.cmd uit en start daarna Start Oranje.cmd. Een nieuwe installatie bevat geen bewoners; herstel zo nodig een eigen volledige back-up via Instellingen.

## Auto mail in Outlook

Bij Afrekeningen opent **Auto mail** een concept in de klassieke Outlook voor Windows op dezelfde pc. De bewindvoerder, het onderwerp, de mailtekst en de opgeslagen pdf-bijlage worden ingevuld. Controleer ook de afzendrekening in Outlook. Je verstuurt zelf en klikt daarna in de app op **Verstuurd registreren**. Een concept openen verandert de verzendstatus niet. De nieuwe Outlook en webmail ondersteunen deze lokale Windows-koppeling niet. Bij een fout controleer je eerst de map Concepten; kopiëren en pdf downloaden blijven mogelijk.

## Publicatie van een volgende versie

Pas VERSION aan en voer `python scripts/build_github.py` uit in de ontwikkelmap. Publiceer de opgeschoonde inhoud van github-publicatie als één commit, inclusief releases/latest.json en het nieuwe updatebestand. De Windows-updater haalt steeds de versie uit latest.json op. Oude updatebestanden blijven beschikbaar.

Publiceer nooit de ontwikkelmap met de echte database, runtime.json, contactseed, persoonlijke tests of back-ups. De bouwstap maakt een openbare kopie met een lege contactseed. Alle berekeningen en gegevensverwerking gebeuren lokaal.
''',encoding='utf-8')
    (destination/'VALIDATIE.md').write_text('De update wordt lokaal getest met tijdelijke administraties. Controleer na installatie de eigen afzendergegevens en vergelijk een maand met de bestaande administratie.\n',encoding='utf-8')
    # Public package documentation uses fictitious examples only.
    for path in (destination/'update').glob('*'):
        if path.is_file():path.write_text(path.read_text(encoding='utf-8').replace('Gino-Robaeys','Voorbeeld-Bewoner'),encoding='utf-8')
    # Build the release using only sanitized files from this publication tree.
    spec=importlib.util.spec_from_file_location('public_package',destination/'scripts/build_update.py')
    public=importlib.util.module_from_spec(spec);spec.loader.exec_module(public)
    releases=destination/'releases';releases.mkdir(exist_ok=True)
    asset=public.build(releases/f'Oranje-update-{version}.zip')
    metadata={'app':'oranje-maaltijden','version':version,'download_url':RAW+'releases/'+asset.name,'sha256':hashlib.sha256(asset.read_bytes()).hexdigest()}
    (releases/'latest.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    return destination

if __name__=='__main__':print(build())
