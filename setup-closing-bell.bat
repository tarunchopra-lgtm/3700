@echo off
REM Closing Bell Task Scheduler Setup Script
REM Run this as Administrator to create the scheduled task

setlocal enabledelayedexpansion

REM Get the workspace directory (assuming this script is in the root)
for %%I in ("%~dp0.") do set WORKSPACE=%%~fI

REM Python executable path
set PYTHON_EXE=%WORKSPACE%\.venv\Scripts\python.exe

REM Task name
set TASK_NAME=ClosingBell

REM Check if running as administrator
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo This script must be run as Administrator.
    echo Please right-click Command Prompt and select "Run as administrator"
    pause
    exit /b 1
)

echo.
echo ========================================
echo Closing Bell Task Scheduler Setup
echo ========================================
echo.
echo Workspace: %WORKSPACE%
echo Python: %PYTHON_EXE%
echo Task Name: %TASK_NAME%
echo.

REM Check if Python exists
if not exist "%PYTHON_EXE%" (
    echo ERROR: Python executable not found at %PYTHON_EXE%
    pause
    exit /b 1
)

echo Creating scheduled task "%TASK_NAME%"...
echo.

REM Create the scheduled task
REM Runs daily at 8:00 AM MST (before market open)
REM Closes when market closes (automatically at 4:00 PM based on script logic)

powershell -Command ^
"$action = New-ScheduledTaskAction -Execute '%PYTHON_EXE%' -Argument 'strategies\closing_bell.py' -WorkingDirectory '%WORKSPACE%'; ^
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM; ^
$settings = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; ^
Register-ScheduledTask -TaskName '%TASK_NAME%' -Action $action -Trigger $trigger -Settings $settings -Description 'Auto-close all trading positions at 12:55 PM MST' -Force; ^
Write-Host 'Task created successfully!'" 2>nul

if %errorlevel% equ 0 (
    echo.
    echo ✓ Task "%TASK_NAME%" created successfully!
    echo.
    echo Next steps:
    echo 1. Open Task Scheduler (taskschd.msc)
    echo 2. Find "%TASK_NAME%" in Library
    echo 3. Verify the task runs daily at 8:00 AM
    echo.
) else (
    echo.
    echo ERROR: Failed to create task
    echo Please run the PowerShell commands manually (see setup-closing-bell.ps1)
    echo.
)

pause
