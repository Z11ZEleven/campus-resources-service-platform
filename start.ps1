# Windows PowerShell 5.1 / PowerShell 7. Arguments are forwarded to dev.py.
$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'scripts\dev.py'

$candidates = @(
    @{ Command = 'py'; Prefix = @('-3.12') },
    @{ Command = 'py'; Prefix = @('-3.11') },
    @{ Command = 'python'; Prefix = @() },
    @{ Command = 'python3.12'; Prefix = @() },
    @{ Command = 'python3.11'; Prefix = @() }
)

foreach ($candidate in $candidates) {
    if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) { continue }
    $prefix = @($candidate.Prefix)
    try {
        & $candidate.Command @prefix -c 'import sys,sysconfig; sys.exit(sys.version_info[:2] not in {(3, 11), (3, 12)} or not sysconfig.get_platform().startswith(''win''))' 2>$null
    } catch { continue }
    if ($LASTEXITCODE -eq 0) {
        & $candidate.Command @prefix $launcher @args
        exit $LASTEXITCODE
    }
}

Write-Host 'Native Windows Python 3.11 or 3.12 was not found. Install Python 3.12 (recommended) or 3.11 with the Python Launcher or add it to PATH (MSYS2 Python is not supported).'
exit 1
