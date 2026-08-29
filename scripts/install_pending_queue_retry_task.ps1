param(
    [string]$TaskName = 'CodexMobileDashboard-PendingQueueRetry',
    [ValidateRange(1, 60)]
    [int]$IntervalMinutes = 1,
    [string]$ConfigPath = "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini",
    [switch]$Preview
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Path $PSScriptRoot -Parent
$runnerPath = Join-Path -Path $repositoryRoot -ChildPath 'scripts\run_pending_queue_retry.ps1'
$powerShellPath = (Get-Command -Name powershell.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw 'Retry runner script was not found.'
}

$quote = [char]34
$taskCommand = $quote + $powerShellPath + $quote + ' -NoProfile -ExecutionPolicy Bypass -File ' + $quote + $runnerPath + $quote + ' -ConfigPath ' + $quote + $ConfigPath + $quote
$createArguments = @('/Create', '/TN', $TaskName, '/TR', $taskCommand, '/SC', 'MINUTE', '/MO', $IntervalMinutes, '/RU', $env:USERNAME, '/RL', 'LIMITED', '/IT', '/F')

if ($Preview) {
    Write-Output "Would register: $TaskName (every $IntervalMinutes minute(s), only while the current user is logged on)"
    exit 0
}

& schtasks.exe @createArguments
if ($LASTEXITCODE -ne 0) {
    throw "Task Scheduler registration failed with exit code $LASTEXITCODE."
}
Write-Output "Registered: $TaskName (every $IntervalMinutes minute(s), only while the current user is logged on)"