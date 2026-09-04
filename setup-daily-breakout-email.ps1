# PowerShell Script to Schedule Daily Breakout Email Task
# Run as Administrator

$TaskName = "3700 Daily Breakout Email"
$ScriptPath = "C:\Users\TarunChopra\3700\strategies\find_daily_trend.py"
$WorkingDirectory = "C:\Users\TarunChopra\3700"
$PythonExe = "$WorkingDirectory\.venv\Scripts\python.exe"
$ScheduledTime = "06:30"

# Verify files exist
if (-not (Test-Path $ScriptPath)) {
    Write-Error "Script not found: $ScriptPath"
    exit 1
}

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python executable not found: $PythonExe"
    exit 1
}

Write-Host "Setting up scheduled task: $TaskName" -ForegroundColor Green
Write-Host "Schedule: Weekdays at $ScheduledTime (6:30 AM)"
Write-Host "Script: $ScriptPath"
Write-Host "Working Directory: $WorkingDirectory"
Write-Host ""

# Remove existing task if it exists
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    Write-Host "Removing existing task: $TaskName" -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Start-Sleep -Seconds 1
}

# Create trigger for weekdays (Mon-Fri) at 6:30 AM
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At $ScheduledTime

# Create action to run Python script with --email flag
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "strategies/find_daily_trend.py --email" `
    -WorkingDirectory $WorkingDirectory

# Create task settings
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew

# Create the scheduled task
Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -Settings $Settings `
    -Description "Scans for daily downtrend breakouts and emails results to tarun.chopra@gmail.com" `
    -ErrorAction Stop | Out-Null

Write-Host "✓ Scheduled task created successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "Task Details:" -ForegroundColor Cyan
Write-Host "  Name: $TaskName"
Write-Host "  Schedule: Weekdays (Mon-Fri) at 06:30 AM"
Write-Host "  Action: python strategies/find_daily_trend.py --email"
Write-Host "  Email: Results sent to tarun.chopra@gmail.com"
Write-Host "  Working Dir: $WorkingDirectory"
Write-Host ""
Write-Host "To verify the task:" -ForegroundColor Cyan
Write-Host "  Get-ScheduledTask -TaskName 3700_Daily_Breakout_Email | Format-List"
Write-Host ""
Write-Host "To run immediately for testing:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName 3700_Daily_Breakout_Email"
Write-Host ""
Write-Host "To view task history:" -ForegroundColor Cyan
Write-Host "  Get-ScheduledTask -TaskName 3700_Daily_Breakout_Email | Get-ScheduledTaskInfo | Format-List"
Write-Host ""
Write-Host "To disable/enable:" -ForegroundColor Cyan
Write-Host "  Disable-ScheduledTask -TaskName 3700_Daily_Breakout_Email"
Write-Host "  Enable-ScheduledTask -TaskName 3700_Daily_Breakout_Email"
