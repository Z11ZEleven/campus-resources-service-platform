# Windows PowerShell 5.1 / PowerShell 7. Arguments are forwarded to dev.py.
$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'scripts\dev.py'

foreach ($candidate in @('py', 'python', 'python3.12')) {
    if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
    $prefix = @()
    if ($candidate -eq 'py') { $prefix = @('-3.12') }
    try {
        & $candidate @prefix -c 'import sys,sysconfig; sys.exit(sys.version_info[:2] != (3, 12) or not sysconfig.get_platform().startswith(''win''))' 2>$null
    } catch { continue }
    if ($LASTEXITCODE -eq 0) {
        & $candidate @prefix $launcher @args
        exit $LASTEXITCODE
    }
}

Write-Host 'Native Windows Python 3.12 was not found. Install Python 3.12 with the Python Launcher or add it to PATH (MSYS2 Python is not supported).'
exit 1
