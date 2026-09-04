# Scheduled Tasks Management Guide

## ⚡ QUICK COMMANDS (Copy & Paste)

**Update ClosingBell to 12:26 PM:**
```powershell
$Time = "12:26"; $Trigger = New-ScheduledTaskTrigger -Daily -At $Time; Set-ScheduledTask -TaskName "ClosingBell" -Trigger $Trigger; Write-Host "✓ ClosingBell updated to $Time"
```

**Update ANY task time (template):**
```powershell
$Time = "HH:MM"; $Trigger = New-ScheduledTaskTrigger -Daily -At $Time; Set-ScheduledTask -TaskName "TaskName" -Trigger $Trigger; Write-Host "✓ Updated to $Time"
```

**Replace:**
- `"HH:MM"` with time in 24-hour format (e.g., `"12:26"`, `"14:30"`, `"06:31"`)
- `"TaskName"` with the task name (e.g., `"ClosingBell"`, `"3700 Daily Breakout Email"`)

---

## Quick Setup

### Option 1: Using PowerShell (Recommended)
```powershell
# Run as Administrator:
powershell -ExecutionPolicy Bypass -File setup-closing-bell.ps1
```

### Option 2: Using Batch File
```cmd
# Run as Administrator:
setup-closing-bell.bat
```

---

## VIEWING SCHEDULED TASKS

### View all scheduled tasks
```powershell
Get-ScheduledTask | Format-Table TaskName, State, Description
```

### View specific task details
```powershell
Get-ScheduledTask -TaskName "ClosingBell" | Format-List
```

### View task run history and last status
```powershell
Get-ScheduledTask -TaskName "ClosingBell" | Get-ScheduledTaskInfo | Format-List
```

### View all your scheduled tasks
```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -match "ClosingBell|Balance|Daily" } | Format-Table TaskName, State, Description
```

---

## ENABLE / DISABLE TASKS

### Quick Enable/Disable Commands

```powershell
# ENABLE (turn on automatic running)
Enable-ScheduledTask -TaskName "ClosingBell"
Enable-ScheduledTask -TaskName "3700 Daily Account Stat"
Enable-ScheduledTask -TaskName "3700 Daily Breakout Email"

# DISABLE (turn off automatic running)
Disable-ScheduledTask -TaskName "ClosingBell"
Disable-ScheduledTask -TaskName "3700 Daily Account Stat"
Disable-ScheduledTask -TaskName "3700 Daily Breakout Email"
```

### Check if task is enabled
```powershell
(Get-ScheduledTask -TaskName "ClosingBell").State
# Output: Enabled or Disabled
```

---

## RUNNING TASKS MANUALLY

### Run a task immediately (for testing)
```powershell
Start-ScheduledTask -TaskName "ClosingBell"
```

### Wait and check if task completed
```powershell
Start-ScheduledTask -TaskName "ClosingBell"
Start-Sleep -Seconds 10
Get-ScheduledTask -TaskName "ClosingBell" | Get-ScheduledTaskInfo | Select-Object LastTaskResult, LastRunTime
```

---

## TASK MANAGEMENT

### Modify task trigger (change time) - EASY WAY

**To change ANY task time, just copy-paste and replace the time:**

```powershell
# Change ClosingBell to ANY time (e.g., 12:26 PM)
$Time = "12:40"; $Trigger = New-ScheduledTaskTrigger -Daily -At $Time; Set-ScheduledTask -TaskName "ClosingBell" -Trigger $Trigger; Write-Host "✓ Updated to $Time"


$Time = "12:39"; $Trigger = New-ScheduledTaskTrigger -Daily -At $Time; Set-ScheduledTask -TaskName "ClosingBell" -Trigger $Trigger -Description "Closing Bell - Runs daily at $Time"; Write-Host "✓ ClosingBell updated to $Time"

# Change Daily Breakout Email to ANY time (e.g., 6:31 AM)
$Time = "06:31"; $Trigger = New-ScheduledTaskTrigger -Daily -At $Time; Set-ScheduledTask -TaskName "3700 Daily Breakout Email" -Trigger $Trigger; Write-Host "✓ Updated to $Time"
```

**How to use:**
1. Replace `"12:26"` with your desired time in 24-hour format (e.g., `"14:30"` for 2:30 PM)
2. Replace `"ClosingBell"` with the task name you want to change
3. Copy-paste the entire line into PowerShell
4. Done!

**Common times:**
```powershell
# 6:30 AM
$Time = "06:30"

# 12:26 PM (noon + 26 minutes)
$Time = "12:26"

# 1:55 PM
$Time = "13:55"

# 3:00 PM
$Time = "15:00"

# 4:15 PM
$Time = "16:15"
```

### Modify task trigger (change time) - Detailed way
```powershell
# Example: Change to run at 7:00 AM instead of 8:00 AM
$task = Get-ScheduledTask -TaskName "ClosingBell"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00AM
Set-ScheduledTask -TaskName "ClosingBell" -Trigger $trigger
```

### Delete/Remove a task
```powershell
Unregister-ScheduledTask -TaskName "ClosingBell" -Confirm:$false
```

### Create custom PowerShell alias for easy commands
Add this to your PowerShell profile:
```powershell
Set-Alias -Name "TaskList" -Value { Get-ScheduledTask | Format-Table TaskName, State }
Set-Alias -Name "TaskInfo" -Value { param($name) Get-ScheduledTask -TaskName $name | Get-ScheduledTaskInfo }
```

---

## CHECKING LOGS

### View closing_bell.py log file
```cmd
type C:\Users\TarunChopra\3700\strategies\closing_bell_log.txt
```

### Monitor log in real-time (like tail -f)
```powershell
Get-Content C:\Users\TarunChopra\3700\strategies\closing_bell_log.txt -Wait
```

### Check Windows Event Viewer for task errors
```cmd
# Open Event Viewer and navigate to:
# Windows Logs > System > Look for Task Scheduler events
eventvwr.msc
```

---

## USEFUL BATCH COMMANDS FOR QUICK ACCESS

Save these as `.bat` files for quick access:

### enable-closing-bell.bat
```batch
@echo off
powershell -Command "Enable-ScheduledTask -TaskName 'ClosingBell'" && echo ✓ ClosingBell enabled
pause
```

### disable-closing-bell.bat
```batch
@echo off
powershell -Command "Disable-ScheduledTask -TaskName 'ClosingBell'" && echo ✓ ClosingBell disabled
pause
```

### run-closing-bell-now.bat
```batch
@echo off
powershell -Command "Start-ScheduledTask -TaskName 'ClosingBell'" && echo ✓ ClosingBell started
echo (Check log file in 30 seconds)
pause
```

### view-all-tasks.bat
```batch
@echo off
powershell -Command "Get-ScheduledTask | Format-Table TaskName, State, Description"
pause
```

---

## EXISTING BALANCE CHECK TASK

To find and manage your balance check task:

```powershell
# Find balance-related tasks
Get-ScheduledTask | Where-Object { $_.TaskName -match "Balance|balance" }

# View balance task details
Get-ScheduledTask -TaskName "YourBalanceTaskName" | Format-List

# Enable/Disable balance task
Enable-ScheduledTask -TaskName "YourBalanceTaskName"
Disable-ScheduledTask -TaskName "YourBalanceTaskName"
```

---

## VIEWING SCHEDULED TASKS IN GUI

### Open Task Scheduler GUI
```cmd
taskschd.msc
```

Then navigate to:
- **Task Scheduler Library** → Find "ClosingBell" or "Balance" tasks
- Right-click a task for options: Enable, Disable, Run, Delete, Properties
- Double-click a task to see detailed configuration
- Select a task and view **History** tab for past runs

---

## SUMMARY TABLE

| Command | Purpose |
|---------|---------|
| `Get-ScheduledTask` | List all tasks |
| `Get-ScheduledTask -TaskName "ClosingBell"` | View specific task |
| `Enable-ScheduledTask -TaskName "ClosingBell"` | Turn on auto-run |
| `Disable-ScheduledTask -TaskName "ClosingBell"` | Turn off auto-run |
| `Start-ScheduledTask -TaskName "ClosingBell"` | Run immediately |
| `Unregister-ScheduledTask -TaskName "ClosingBell"` | Delete task |
| `taskschd.msc` | Open GUI Task Scheduler |

---

## TROUBLESHOOTING

### Task not running at scheduled time?
1. Check if task is Enabled: `Get-ScheduledTask -TaskName "ClosingBell" | Select State`
2. Check last error: `Get-ScheduledTask -TaskName "ClosingBell" | Get-ScheduledTaskInfo | Select LastTaskResult`
3. Check Event Viewer for errors: `eventvwr.msc`

### "Access Denied" error?
- Run PowerShell as Administrator
- Right-click PowerShell → "Run as administrator"

### Task runs but Python script errors?
- Check the log file: `C:\Users\TarunChopra\3700\strategies\closing_bell_log.txt`
- Run script manually to test: `python C:\Users\TarunChopra\3700\strategies\closing_bell.py`



Get-ScheduledTask | Where-Object { $_.TaskName -match "ClosingBell|Balance|Daily" } | Format-Table TaskName, State, Description


Get-ScheduledTask | Where-Object { $_.TaskName -match "ClosingBell|Balance|Daily" } | ForEach-Object { 
    $info = Get-ScheduledTaskInfo -TaskName $_.TaskName
    [PSCustomObject]@{
        TaskName = $_.TaskName
        State = $_.State
        NextRunTime = $info.NextRunTime
        Description = $_.Description
    }
} | Format-Table -AutoSize