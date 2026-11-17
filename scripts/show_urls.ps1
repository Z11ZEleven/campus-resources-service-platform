param(
    [ValidateSet('none', 'screen', 'app', 'docs')]
    [string]$Open = 'none'
)

$ErrorActionPreference = 'Stop'

$ip = (
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -eq 'WLAN' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1
).IPAddress

if (-not $ip) {
    $ip = (
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -notlike '127.*' -and
                $_.IPAddress -notlike '169.254.*' -and
                $_.InterfaceAlias -notmatch 'VPN|WSL'
            } |
            Select-Object -First 1
    ).IPAddress
}

function Test-Port([int]$Port) {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $client.Connect('127.0.0.1', $Port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

$rows = @(
    [pscustomobject]@{ Purpose = 'Main app (PC)'; Url = 'http://localhost:4173/' }
    [pscustomobject]@{ Purpose = 'Check-in page'; Url = 'http://localhost:4173/m1/checkin' }
    [pscustomobject]@{ Purpose = 'Classroom screen'; Url = "http://${ip}:4173/screen/1" }
    [pscustomobject]@{ Purpose = 'Phone test home'; Url = "http://${ip}:4173/" }
    [pscustomobject]@{ Purpose = 'API docs'; Url = 'http://127.0.0.1:8001/docs' }
)

"WLAN IP: $ip"
$rows | Format-Table -AutoSize

"Ports: 4173=$(Test-Port 4173) 5173=$(Test-Port 5173) 8001=$(Test-Port 8001) 8002=$(Test-Port 8002) 3306=$(Test-Port 3306)"

switch ($Open) {
    'screen' { Start-Process "http://${ip}:4173/screen/1" }
    'app' { Start-Process 'http://localhost:4173/' }
    'docs' { Start-Process 'http://127.0.0.1:8001/docs' }
}
