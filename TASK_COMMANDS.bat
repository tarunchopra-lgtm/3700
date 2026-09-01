@echo off
REM Quick Task Management Commands
REM Copy and paste these commands into PowerShell (run as Administrator)

REM ======================================
REM ENABLE/DISABLE TASKS
REM ======================================

REM Enable ClosingBell
powershell -Command "Enable-ScheduledTask -TaskName ClosingBell"

REM Disable ClosingBell
powershell -Command "Disable-ScheduledTask -TaskName ClosingBell"

REM Enable Daily Account Stat
powershell -Command "Enable-ScheduledTask -TaskName '3700 Daily Account Stat'"

REM Disable Daily Account Stat
powershell -Command "Disable-ScheduledTask -TaskName '3700 Daily Account Stat'"

REM Enable Daily Breakout Email
powershell -Command "Enable-ScheduledTask -TaskName '3700 Daily Breakout Email'"

REM Disable Daily Breakout Email
powershell -Command "Disable-ScheduledTask -TaskName '3700 Daily Breakout Email'"

REM ======================================
REM VIEW TASKS
REM ======================================

REM View only your trading tasks
powershell -Command "Get-ScheduledTask | Where-Object { $_.TaskName -match '3700|ClosingBell' } | Format-Table TaskName, State, Description"

REM View ClosingBell details
powershell -Command "Get-ScheduledTask -TaskName ClosingBell | Format-List"

REM View task status
powershell -Command "(Get-ScheduledTask -TaskName ClosingBell).State"

REM ======================================
REM RUN TASKS IMMEDIATELY (FOR TESTING)
REM ======================================

REM Run ClosingBell now
powershell -Command "Start-ScheduledTask -TaskName ClosingBell"

REM Run Daily Account Stat now
powershell -Command "Start-ScheduledTask -TaskName '3700 Daily Account Stat'"

REM ======================================
REM DELETE TASKS
REM ======================================

REM Delete ClosingBell
powershell -Command "Unregister-ScheduledTask -TaskName ClosingBell -Confirm:0"

REM Delete Daily Account Stat
powershell -Command "Unregister-ScheduledTask -TaskName '3700 Daily Account Stat' -Confirm:0"
