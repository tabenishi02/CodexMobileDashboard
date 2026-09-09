param(
    [string]$TaskName = 'CodexMobileDashboard-Collector',
    [switch]$Preview
)

$ErrorActionPreference = 'Stop'

if ($Preview) {
    Write-Output "Would unregister: $TaskName"
    exit 0
}

& schtasks.exe /Delete /TN $TaskName /F
if ($LASTEXITCODE -ne 0) {
    throw "Task Scheduler removal failed with exit code $LASTEXITCODE."
}
Write-Output "Unregistered: $TaskName"