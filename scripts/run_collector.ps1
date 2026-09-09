[CmdletBinding()]
param(
    [string]$ConfigPath = "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini",
    [string]$PythonPath = "python"
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Path $PSScriptRoot -Parent
$python = Get-Command -Name $PythonPath -ErrorAction Stop
$mutex = New-Object System.Threading.Mutex($false, 'Local\CodexMobileDashboard-Collector')
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
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
        & $python.Source -m tools.collector --config $ConfigPath collect-once --incremental
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