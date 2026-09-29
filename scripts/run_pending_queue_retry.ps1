[CmdletBinding()]
param(
    [string]$ConfigPath = "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini",
    [string]$MutexName = "Local\CodexMobileDashboard-Collector"
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Path $PSScriptRoot -Parent
$python = Get-Command -Name python -ErrorAction Stop
$mutex = New-Object System.Threading.Mutex($false, $MutexName)
$lockTaken = $false

try {
    try {
        $lockTaken = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $lockTaken = $true
    }
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