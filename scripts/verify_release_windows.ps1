param([Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
python (Join-Path $PSScriptRoot 'verify_release.py') $Output --require-windows
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
