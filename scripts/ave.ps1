$aveToolkitRoot = Split-Path -Parent $PSScriptRoot
$aveLocalPython = Join-Path $aveToolkitRoot '.venv/Scripts/python.exe'
if (Test-Path -LiteralPath $aveLocalPython) {
    & $aveLocalPython (Join-Path $aveToolkitRoot 'avtool.py') @args
} else {
    & python (Join-Path $aveToolkitRoot 'avtool.py') @args
}
exit $LASTEXITCODE
