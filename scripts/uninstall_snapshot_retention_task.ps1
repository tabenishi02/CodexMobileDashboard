param(
    [string]$TaskName = 'CodexMobileDashboard-SnapshotRetention',
    [switch]$Preview
)
$ErrorActionPreference = 'Stop'
if ($TaskName -notmatch '^CodexMobileDashboard-SnapshotRetention(?:-|$)') { throw 'Snapshot retention task name required.' }
if ($Preview) {
    Write-Output "Would unregister: $TaskName"
    exit 0
}
& schtasks.exe /Delete /TN $TaskName /F
if ($LASTEXITCODE -ne 0) { throw "Task Scheduler removal failed with exit code $LASTEXITCODE." }
Write-Output "Unregistered: $TaskName"