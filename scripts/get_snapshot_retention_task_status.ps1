param([string]$TaskName = 'CodexMobileDashboard-SnapshotRetention')
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskPath '\' | Where-Object { $_.TaskName -eq $TaskName }
if ($null -eq $task) {
    [pscustomobject]@{ TaskName = $TaskName; Registered = $false }
    return
}
$info = Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath '\'
[pscustomobject]@{
    TaskName = $task.TaskName
    Registered = $true
    Enabled = $task.Settings.Enabled
    State = [string]$task.State
    LastRunTime = $info.LastRunTime
    NextRunTime = $info.NextRunTime
    LastTaskResult = $info.LastTaskResult
    MissedRuns = $info.NumberOfMissedRuns
    WeeksInterval = $task.Triggers.WeeksInterval
    DaysOfWeek = $task.Triggers.DaysOfWeek
    StartBoundary = $task.Triggers.StartBoundary
    LogonType = [string]$task.Principal.LogonType
    StartWhenAvailable = $task.Settings.StartWhenAvailable
    MultipleInstances = [string]$task.Settings.MultipleInstances
}