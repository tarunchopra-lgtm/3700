# Closing Bell Task Scheduler Setup - PowerShell Script
# Run this as Administrator

$WorkspaceRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $WorkspaceRoot ".venv\Scripts\python.exe"
$TaskName = "ClosingBell"
$ScriptPath = "strategies\closing_bell.py"

Write-Host "========================================" 
Write-Host "Closing Bell Task Scheduler Setup" 
Write-Host "========================================" 
Write-Host ""

# Verify admin privileges
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: This script must run as Administrator" 
    exit 1
}

# Check if Python exists
if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: Python executable not found at $PythonExe" 
    exit 1
}

Write-Host "Workspace:  $WorkspaceRoot" 
Write-Host "Python:     $PythonExe" 
Write-Host "Task Name:  $TaskName" 
Write-Host ""

# Remove existing task if it exists
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Removing existing task '$TaskName'..." 
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create the scheduled task action
$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $ScriptPath `
    -WorkingDirectory $WorkspaceRoot

# Create trigger: Daily at 12:55 PM MST
$trigger = New-ScheduledTaskTrigger -Daily -At 12:55PM

# Configure task settings
$settings = New-ScheduledTaskSettingsSet `
    -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

# Register the task
Write-Host "Creating scheduled task..." 
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Auto-close all trading positions at 12:55 PM MST" `
    -Force | Out-Null

Write-Host "`n✓ Task '$TaskName' created successfully!" 
Write-Host ""
Write-Host "Task Details:" 
Write-Host "  - Runs daily at: 12:55 PM MST" 
Write-Host "  - Auto-closes all open positions" 
Write-Host "  - Logs written to: strategies\closing_bell_log.txt" 
Write-Host ""
Write-Host "========================================" 
Write-Host "QUICK COMMANDS" 
Write-Host "========================================" 
Write-Host ""
Write-Host "Enable:  Enable-ScheduledTask -TaskName ClosingBell" 
Write-Host "Disable: Disable-ScheduledTask -TaskName ClosingBell" 
Write-Host "Run now: Start-ScheduledTask -TaskName ClosingBell" 
Write-Host "View:    Get-ScheduledTask -TaskName ClosingBell" 
Write-Host "Delete:  Unregister-ScheduledTask -TaskName ClosingBell -Confirm:0" 
Write-Host ""
Write-Host "========================================"
