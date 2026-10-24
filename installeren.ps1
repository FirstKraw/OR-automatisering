$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$basePython = (Get-Command python -ErrorAction Stop).Source
& $basePython -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10 of nieuwer vereist'"
if ($LASTEXITCODE -ne 0) { throw 'Installeer Python 3.10 of nieuwer vanaf python.org, met Python in PATH.' }
$venvPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $basePython -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'De lokale Python-omgeving kon niet worden aangemaakt.' }
}
& $venvPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installatie mislukt. Controleer de internetverbinding en probeer opnieuw.' }
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut((Join-Path $PSScriptRoot 'Oranje Maaltijdenbeheer.lnk'))
$shortcut.TargetPath = Join-Path $PSScriptRoot 'Start Oranje.cmd'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'Lokaal maaltijdenbeheer voor Oranje'
$shortcut.Save()
Write-Host 'Installatie klaar. Open Oranje Maaltijdenbeheer.lnk of Start Oranje.cmd.'
