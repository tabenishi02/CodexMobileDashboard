param(
    [string]$TaskName = 'CodexMobileDashboard-Collector',
    [ValidateRange(1, 60)]
    [int]$IntervalMinutes = 1,
    [string]$ConfigPath = "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini",
    [switch]$Preview
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Path $PSScriptRoot -Parent
$runnerPath = Join-Path -Path $repositoryRoot -ChildPath 'scripts\run_collector_hidden.pyw'
$powerShellPath = (Get-Command -Name powershell.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw 'Collector runner script was not found.'
}

$pythonPath = (Get-Command -Name python -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Collector configuration was not found."
}

$pythonwPath = Join-Path (Split-Path $pythonPath -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath -PathType Leaf)) {
    throw 'pythonw.exe was not found beside python.exe.'
}

$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$quote = [char]34
$taskArguments = $quote + $runnerPath + $quote + ' --powershell ' + $quote + $powerShellPath + $quote + ' --python ' + $quote + $pythonPath + $quote + ' --config ' + $quote + $ConfigPath + $quote

if ($Preview) {
    Write-Output "Would register: $TaskName (every $IntervalMinutes minute(s), only while the current user is logged on)"
    exit 0
}

$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument $taskArguments -WorkingDirectory $repositoryRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output "Registered: $TaskName (every $IntervalMinutes minute(s), only while the current user is logged on)"
