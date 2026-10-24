param([string]$Installatiemap = '', [switch]$GeenPauze)
$ErrorActionPreference = 'Stop'
$lease = $null
$archive = $null
$backupPath = $null
$changesStarted = $false
$exitCode = 0
$newFiles = @()

function ChildPath([string]$Base, [string]$Relative) {
    $basePath = [IO.Path]::GetFullPath($Base).TrimEnd('\', '/')
    $candidate = [IO.Path]::GetFullPath((Join-Path $basePath $Relative))
    if (-not $candidate.StartsWith($basePath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Ongeldig bestandspad in het updatepakket.'
    }
    return $candidate
}

function FileSha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '') }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}

try {
    Write-Host 'Oranje Maaltijdenbeheer - programma-update'
    Write-Host 'Sluit eerst de app af: ook het startvenster, niet alleen de browser.'
    $payload = Join-Path $PSScriptRoot 'payload'
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($manifest.app -ne 'oranje-maaltijden' -or $manifest.version -notmatch '^\d{4}\.\d{1,2}\.\d{1,2}\.\d+$' -or @($manifest.files).Count -lt 8) {
        throw 'Ongeldig of onvolledig updatepakket. Pak het zipbestand eerst volledig uit.'
    }
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($item in $manifest.files) {
        if ($item.path -notmatch '^(app\.py|requirements\.txt|start\.ps1|Start Oranje\.cmd|Update ophalen\.cmd|installeren\.ps1|Installeren\.cmd|README\.md|VALIDATIE\.md|VERSION|oranje/[A-Za-z_]+\.py|static/[A-Za-z0-9._-]+)$' -or -not $seen.Add([string]$item.path)) {
            throw 'Het pakket bevat een ongeldig of dubbel bestand.'
        }
        $source = ChildPath $payload $item.path
        if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or (FileSha256 $source) -ne $item.sha256) {
            throw ('Controle mislukt voor ' + $item.path + '. Download en pak het updatepakket opnieuw uit.')
        }
    }
    if (-not $Installatiemap) {
        $parent = Split-Path -Parent $PSScriptRoot
        if (Test-Path -LiteralPath (Join-Path $parent 'app.py')) {
            $Installatiemap = $parent
        } else {
            Add-Type -AssemblyName System.Windows.Forms
            $picker = New-Object System.Windows.Forms.FolderBrowserDialog
            $picker.Description = 'Kies de bestaande map Oranje Maaltijdenbeheer, met app.py en Start Oranje.cmd.'
            $picker.ShowNewFolderButton = $false
            try {
                if ($picker.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { throw 'Update geannuleerd.' }
                $Installatiemap = $picker.SelectedPath
            } finally { $picker.Dispose() }
        }
    }
    $target = [IO.Path]::GetFullPath($Installatiemap).TrimEnd('\', '/')
    if ($target.StartsWith([IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\', '/') + '\', [StringComparison]::OrdinalIgnoreCase) -or $target -eq [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\', '/')) {
        throw 'Kies de bestaande appmap, niet de uitgepakte updatemap of payloadmap.'
    }
    foreach ($required in @('app.py', 'oranje/store.py', 'static/app.js')) {
        if (-not (Test-Path -LiteralPath (ChildPath $target $required) -PathType Leaf)) { throw 'Dit lijkt geen bestaande Oranje-installatie.' }
    }
    $runtime = Join-Path $target '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $runtime)) {
        $runtimeFile = Join-Path $target 'runtime.json'
        if (Test-Path -LiteralPath $runtimeFile) { $runtime = (Get-Content -LiteralPath $runtimeFile -Raw | ConvertFrom-Json).python }
        if (-not $runtime -or -not (Test-Path -LiteralPath $runtime)) { $runtime = (Get-Command python -ErrorAction Stop).Source }
    }
    & $runtime -c "import sys,reportlab,openpyxl; assert sys.version_info >= (3,10)"
    if ($LASTEXITCODE -ne 0) { throw 'De bestaande Python-omgeving werkt niet. Voer eerst Installeren.cmd uit in de appmap.' }
    $dataDir = Join-Path $target 'data'
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $lease = [IO.File]::Open((Join-Path $dataDir '.instance-lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    if ($lease.Length -eq 0) { $lease.WriteByte(48); $lease.Flush() }
    try { $lease.Lock(0,1) } catch { throw 'De app is nog actief. Sluit het startvenster en probeer de update opnieuw.' }
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $backupDir = Join-Path $target 'update-backups'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backupPath = Join-Path $backupDir ('voor-update-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,6) + '.zip')
    $originals = @()
    foreach ($item in $manifest.files) {
        if (Test-Path -LiteralPath (ChildPath $target $item.path) -PathType Leaf) { $originals += [string]$item.path } else { $newFiles += [string]$item.path }
    }
    foreach ($extra in @('data/oranje.sqlite3', 'runtime.json', 'Oranje Maaltijdenbeheer.lnk')) {
        if (Test-Path -LiteralPath (ChildPath $target $extra) -PathType Leaf) { $originals += $extra }
    }
    $zip = [IO.Compression.ZipFile]::Open($backupPath, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($relative in $originals) {
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (ChildPath $target $relative), $relative, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    } finally { $zip.Dispose() }
    Write-Host ('Reservekopie gemaakt: ' + $backupPath)
    $changesStarted = $true
    foreach ($item in $manifest.files) {
        $destination = ChildPath $target $item.path
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath (ChildPath $payload $item.path) -Destination $destination -Force
    }
    Push-Location -LiteralPath $target
    try {
        & $runtime -c "import ast,pathlib,reportlab,openpyxl; [ast.parse(p.read_text(encoding='utf-8')) for p in [pathlib.Path('app.py'),*pathlib.Path('oranje').glob('*.py')]]; from oranje.store import Store; from oranje.exports import invoice_pdf"
        if ($LASTEXITCODE -ne 0) { throw 'Controle van de nieuwe programmabestanden is mislukt.' }
    } finally { Pop-Location }
    try {
        $shellObject = New-Object -ComObject WScript.Shell
        $shortcut = $shellObject.CreateShortcut((Join-Path $target 'Oranje Maaltijdenbeheer.lnk'))
        $shortcut.TargetPath = Join-Path $target 'Start Oranje.cmd'
        $shortcut.WorkingDirectory = $target
        $shortcut.Description = 'Lokaal maaltijdenbeheer voor Oranje'
        $shortcut.Save()
    } catch { Write-Host 'Gebruik Start Oranje.cmd om de app te openen; de snelkoppeling kon niet worden vernieuwd.' }
    Write-Host ''
    Write-Host 'UPDATE GESLAAGD. De administratie en Python-instellingen zijn behouden.' -ForegroundColor Green
    Write-Host '1. Start de app opnieuw en vernieuw de browser met Ctrl+R.'
    Write-Host '2. Controleer de afzendergegevens bij Instellingen.'
    Write-Host '3. Kies de gewenste werkmaand en maak de afrekeningen opnieuw.'
    Write-Host '   Bestaande PDFs blijven als oude versies bewaard.'
} catch {
    Write-Host ('UPDATE NIET VOLTOOID: ' + $_.Exception.Message) -ForegroundColor Red
    if ($changesStarted -and $backupPath -and (Test-Path -LiteralPath $backupPath)) {
        try {
            $archive = [IO.Compression.ZipFile]::OpenRead($backupPath)
            foreach ($entry in $archive.Entries) {
                # Database and runtime were never overwritten; restore only program files.
                if ($entry.FullName -eq 'data/oranje.sqlite3' -or $entry.FullName -eq 'runtime.json') { continue }
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (ChildPath $target $entry.FullName), $true)
            }
            foreach ($relative in $newFiles) {
                $newPath = ChildPath $target $relative
                if (Test-Path -LiteralPath $newPath -PathType Leaf) { Remove-Item -LiteralPath $newPath -Force }
            }
            Write-Host 'De vorige programmabestanden zijn teruggezet. De administratie is behouden.'
        } catch { Write-Host ('Automatisch terugzetten is mislukt. Bewaar de reservekopie: ' + $backupPath) -ForegroundColor Red }
    }
    $exitCode = 1
} finally {
    if ($archive) { $archive.Dispose() }
    if ($lease) { $lease.Dispose() }
}
if (-not $GeenPauze) { Read-Host 'Druk op Enter om dit venster te sluiten' | Out-Null }
exit $exitCode
