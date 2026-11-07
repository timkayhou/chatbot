# Register (or remove) the logon start and daily maintenance for this user.
# Falls back to the per-user Startup folder when Task Scheduler needs elevation.
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$workbenchTask = "LocalSupportWorkbench"
$maintenanceTask = "LocalSupportWorkbenchMaintenance"
$startupCmd = Join-Path ([Environment]::GetFolderPath('Startup')) "LocalSupportWorkbench.cmd"

if ($Remove) {
    foreach ($name in @($workbenchTask, $maintenanceTask)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
        }
        Write-Output "removed task: $name"
    }
    if (Test-Path $startupCmd) { Remove-Item $startupCmd -Force; Write-Output "removed: $startupCmd" }
    exit 0
}

$root = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $root "scripts\start_workbench.ps1"
$maintenanceScript = Join-Path $root "scripts\maintenance.ps1"

$logonAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn
try {
    Register-ScheduledTask -TaskName $workbenchTask -Action $logonAction -Trigger $logonTrigger `
        -Description "Start the local support workbench on logon (loopback only)" -Force | Out-Null
    Write-Output "registered task: $workbenchTask"
} catch {
    $command = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`"`r`n"
    Set-Content -Path $startupCmd -Value $command -Encoding ASCII
    Write-Output "task registration denied; installed startup entry: $startupCmd"
}

$maintenanceAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$maintenanceScript`""
$maintenanceTrigger = New-ScheduledTaskTrigger -Daily -At "03:30"
try {
    Register-ScheduledTask -TaskName $maintenanceTask -Action $maintenanceAction -Trigger $maintenanceTrigger `
        -Description "Backup the database and rotate local logs" -Force | Out-Null
    Write-Output "registered task: $maintenanceTask"
} catch {
    Write-Output "maintenance task denied; the workbench still backs up hourly at startup (scripts\maintenance.ps1 can be run manually)"
}

Get-ScheduledTask -TaskName $workbenchTask, $maintenanceTask -ErrorAction SilentlyContinue |
    Select-Object TaskName, State |
    Format-Table -AutoSize
