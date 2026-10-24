param([string]$Installatiemap = '', [string]$KanaalUrl = 'https://raw.githubusercontent.com/FirstKraw/OR-automatisering/main/releases/latest.json', [string]$TijdelijkeMap = '', [switch]$GeenPauze)
$ErrorActionPreference = 'Stop'
$exitCode = 0
$stage = $null
$target = $null
function Download([string]$Url, [string]$Destination, [long]$Limit) {
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.UserAgent = 'Oranje-Maaltijdenbeheer-Updater'
    $request.Timeout = 30000
    $request.ReadWriteTimeout = 30000
    $response = $null; $inputStream = $null; $outputStream = $null
    try {
        $response = $request.GetResponse()
        if ($response.ContentLength -gt $Limit) { throw 'Het downloadbestand is te groot.' }
        $inputStream = $response.GetResponseStream()
        $outputStream = [IO.File]::Create($Destination)
        $buffer = New-Object byte[] 16384
        $total = 0
        while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $total += $count
            if ($total -gt $Limit) { throw 'De download overschrijdt de toegestane grootte.' }
            $outputStream.Write($buffer, 0, $count)
        }
    } finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
    }
}
function Hash([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $sha.Dispose(); $stream.Dispose() }
}
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host 'Oranje - Update ophalen van GitHub'
    Write-Host 'Sluit eerst de app volledig af, ook het zwarte startvenster.'
    if (-not $Installatiemap) {
        $candidate = $env:ORANJE_UPDATE_DIR
        if ($candidate -and (Test-Path -LiteralPath (Join-Path $candidate 'app.py'))) { $Installatiemap = $candidate }
        else {
            Add-Type -AssemblyName System.Windows.Forms
            $picker = New-Object System.Windows.Forms.FolderBrowserDialog
            $picker.Description = 'Kies de bestaande Oranje-appmap met app.py en Start Oranje.cmd.'
            $picker.ShowNewFolderButton = $false
            try {
                if ($picker.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { throw 'Update geannuleerd.' }
                $Installatiemap = $picker.SelectedPath
            } finally { $picker.Dispose() }
        }
    }
    $target = [IO.Path]::GetFullPath($Installatiemap)
    if ($target.Length -gt [IO.Path]::GetPathRoot($target).Length) { $target = $target.TrimEnd('\', '/') }
    if (-not (Test-Path -LiteralPath (Join-Path $target 'app.py')) -or -not (Test-Path -LiteralPath (Join-Path $target 'oranje/store.py'))) { throw 'Kies de bestaande appmap.' }
    if (-not $TijdelijkeMap) { $TijdelijkeMap = [IO.Path]::GetTempPath() }
    $stage = Join-Path $TijdelijkeMap ('oranje-github-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    $metadataFile = Join-Path $stage 'latest.json'
    $separator = '?'
    if ($KanaalUrl.Contains('?')) { $separator = '&' }
    Download ($KanaalUrl + $separator + 'controle=' + [DateTime]::UtcNow.Ticks) $metadataFile 1048576
    $metadata = [IO.File]::ReadAllText($metadataFile) | ConvertFrom-Json
    if ($metadata.app -ne 'oranje-maaltijden' -or $metadata.version -notmatch '^\d{4}\.\d{1,2}\.\d{1,2}\.\d+$' -or $metadata.sha256 -notmatch '^[A-Fa-f0-9]{64}$') { throw 'Ongeldige update-informatie ontvangen.' }
    $sourceUri = [Uri]$KanaalUrl
    $assetUri = [Uri]$metadata.download_url
    if ($assetUri.Scheme -ne $sourceUri.Scheme -or $assetUri.Host -ne $sourceUri.Host -or $assetUri.Port -ne $sourceUri.Port) { throw 'Het updatebestand staat niet op de verwachte bron.' }
    if ($sourceUri.Scheme -ne 'https' -and -not $sourceUri.IsLoopback) { throw 'De updatebron moet HTTPS gebruiken.' }
    $versionFile = Join-Path $target 'VERSION'
    if (Test-Path -LiteralPath $versionFile) {
        $localVersion = [IO.File]::ReadAllText($versionFile).Trim()
        if ($localVersion -match '^\d{4}\.\d{1,2}\.\d{1,2}\.\d+$' -and [Version]$localVersion -ge [Version]$metadata.version) {
            Write-Host ('Deze app is al bijgewerkt: versie ' + $localVersion) -ForegroundColor Green
            $stage = $null
            if (-not $GeenPauze) { Read-Host 'Druk op Enter om te sluiten' | Out-Null }
            exit 0
        }
    }
    Write-Host ('Nieuwe versie: ' + $metadata.version)
    $zipFile = Join-Path $stage 'update.zip'
    Download $metadata.download_url $zipFile 104857600
    if ((Hash $zipFile) -ne $metadata.sha256) { throw 'De downloadcontrole is mislukt. Er is niets gewijzigd.' }
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $expanded = Join-Path $stage 'pakket'
    New-Item -ItemType Directory -Path $expanded | Out-Null
    $archive = [IO.Compression.ZipFile]::OpenRead($zipFile)
    $names = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $totalSize = 0
    try {
        foreach ($entry in $archive.Entries) {
            $totalSize += $entry.Length
            if ($totalSize -gt 209715200) { throw 'Het uitgepakte updatebestand is te groot.' }
            $destination = [IO.Path]::GetFullPath((Join-Path $expanded $entry.FullName))
            if (-not $destination.StartsWith($expanded + '\', [StringComparison]::OrdinalIgnoreCase) -or -not $names.Add($destination)) { throw 'Ongeldig pad in het updatebestand.' }
            if ($entry.FullName.EndsWith('/')) { New-Item -ItemType Directory -Path $destination -Force | Out-Null; continue }
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $false)
        }
    } finally { $archive.Dispose() }
    $manifest = [IO.File]::ReadAllText((Join-Path $expanded 'manifest.json')) | ConvertFrom-Json
    if ($manifest.app -ne 'oranje-maaltijden' -or $manifest.version -ne $metadata.version) { throw 'De versie van het pakket komt niet overeen met de download.' }
    $installer = Join-Path $expanded 'Update.ps1'
    if (-not (Test-Path -LiteralPath $installer)) { throw 'De update bevat geen installatiebestand.' }
    $output = & powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File $installer -Installatiemap $target -GeenPauze 2>&1 | Out-String
    $installerExit = $LASTEXITCODE
    Write-Host $output
    [IO.File]::WriteAllText((Join-Path $target 'github-update.log'), $output)
    if ($installerExit -ne 0) { throw 'De installatie is niet voltooid. Zie github-update.log in de appmap.' }
    Write-Host 'Start Oranje.cmd opnieuw en vernieuw de browser met Ctrl+R.' -ForegroundColor Green
} catch {
    Write-Host ('UPDATE MISLUKT: ' + $_.Exception.Message) -ForegroundColor Red
    if ($target) {
        try { [IO.File]::AppendAllText((Join-Path $target 'github-update.log'), "`r`n" + $_.Exception.Message) } catch {}
    }
    $exitCode = 1
} finally {
    # Leave the small staging folder for troubleshooting; never recursively delete a computed path.
}
if (-not $GeenPauze) { Read-Host 'Druk op Enter om dit venster te sluiten' | Out-Null }
exit $exitCode
