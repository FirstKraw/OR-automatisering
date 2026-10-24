$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtimeFile = Join-Path $PSScriptRoot 'runtime.json'
$localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (Test-Path -LiteralPath $localPython) {
    $appPython = $localPython
} elseif (Test-Path -LiteralPath $runtimeFile) {
    $appPython = (Get-Content -LiteralPath $runtimeFile -Raw | ConvertFrom-Json).python
} else {
    $appPython = (Get-Command python -ErrorAction Stop).Source
}
if (-not (Test-Path -LiteralPath $appPython)) {
    throw 'Python niet gevonden. Voer Installeren.cmd uit.'
}
& $appPython (Join-Path $PSScriptRoot 'app.py')
exit $LASTEXITCODE
