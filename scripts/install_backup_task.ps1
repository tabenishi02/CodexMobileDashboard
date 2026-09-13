param(
    [string]$TaskName = 'CodexMobileDashboard-Backup',
    [ValidateSet('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')]
    [string]$Day = 'Sunday',
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$At = '03:00',
    [string]$ConfigPath = "$env:LOCALAPPDATA\CodexMobileDashboard\config\backup.ini",
    [switch]$Preview
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Path $PSScriptRoot -Parent
$runnerPath = Join-Path -Path $repositoryRoot -ChildPath 'scripts\run_backup_hidden.pyw'
if ($TaskName -notmatch '^CodexMobileDashboard-Backup(?:-|$)') { throw 'Backup task name required.' }

if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw 'Backup runner script was not found.'
}

$pythonPath = (Get-Command -Name python -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Backup configuration was not found."
}

$pythonwPath = Join-Path (Split-Path $pythonPath -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath -PathType Leaf)) {
    throw 'pythonw.exe was not found beside python.exe.'
}

$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$quote = [char]34
$taskArguments = $quote + $runnerPath + $quote + ' --python ' + $quote + $pythonPath + $quote + ' --config ' + $quote + $ConfigPath + $quote

if ($Preview) {
    Write-Output "Would register: $TaskName (weekly $Day $At, only while the current user is logged on)"
    exit 0
}

$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument $taskArguments -WorkingDirectory $repositoryRoot
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $Day -At $At
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output "Registered: $TaskName (weekly $Day $At, only while the current user is logged on)"
