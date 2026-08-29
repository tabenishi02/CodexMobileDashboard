[CmdletBinding()]
param(
    [string]$ConfigPath = "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini"
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Path $PSScriptRoot -Parent
$python = Get-Command -Name python -ErrorAction Stop
$mutex = New-Object System.Threading.Mutex($false, 'Local\CodexMobileDashboard-PendingQueueRetry')
$lockTaken = $false

try {
    $lockTaken = $mutex.WaitOne(0)
    if (-not $lockTaken) {
        exit 0
    }

    Push-Location -LiteralPath $repositoryRoot
    try {
        & $python.Source -m tools.collector --config $ConfigPath retry-queued --all
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($lockTaken) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}