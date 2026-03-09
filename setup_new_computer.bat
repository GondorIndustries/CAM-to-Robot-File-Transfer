@echo off
setlocal EnableDelayedExpansion
title IRC5 Pipeline - New Computer Setup
cd /d "%~dp0"

echo.
echo ============================================================
echo  IRC5 Pipeline - New Computer Setup
echo ============================================================
echo.
echo This script will:
echo   1. Check Python and Google Drive are installed
echo   2. Set a static IP on the Ethernet adapter (for IRC5)
echo   3. Test the connection to the IRC5 controller
echo   4. Set the pipeline to start automatically on login
echo.
pause

SET SCRIPT_DIR=%~dp0
SET IRC5_IP=192.168.125.2
SET IRC5_SUBNET=255.255.255.0
SET IRC5_CONTROLLER=192.168.125.1
SET ERRORS=0

echo.
echo ── Step 1: Checking requirements ──────────────────────────
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [MISSING] Python is not installed or not on PATH.
    echo           Download from: https://www.python.org/downloads/
    echo           IMPORTANT: tick "Add Python to PATH" during install.
    SET ERRORS=1
) else (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK]     %%v
)

:: Check Google Drive G: mount
if exist "G:\My Drive\RobotInbox\Inbox" (
    echo [OK]     Google Drive mounted at G:\ — Inbox folder found
) else if exist "G:\My Drive" (
    echo [WARN]   Google Drive is at G:\My Drive but RobotInbox\Inbox folder
    echo          is missing. Creating it now...
    mkdir "G:\My Drive\RobotInbox\Inbox" 2>nul
    mkdir "G:\My Drive\RobotInbox\Processed" 2>nul
    echo [OK]     Created G:\My Drive\RobotInbox\Inbox
) else (
    echo [MISSING] Google Drive for Desktop is not installed or not signed in.
    echo           Download from: https://drive.google.com/
    echo           Sign in with the Gondor Industries Google account.
    echo           After setup, the G: drive should appear in File Explorer.
    SET ERRORS=1
)

if !ERRORS! NEQ 0 (
    echo.
    echo One or more requirements are missing. Install them and run this
    echo script again.
    echo.
    pause
    exit /b 1
)

echo.
echo ── Step 2: Configuring Ethernet static IP ─────────────────
echo.
echo Setting Ethernet adapter to 192.168.125.2 / 255.255.255.0
echo (This is the fixed IP the IRC5 controller expects to talk to.)
echo.

:: Find the name of the active Ethernet adapter
SET ETH_ADAPTER=
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-NetAdapter | Where-Object {$_.InterfaceDescription -like '*Ethernet*' -and $_.Status -eq 'Up'} | Select-Object -First 1 -ExpandProperty Name" 2^>nul') do (
    SET ETH_ADAPTER=%%a
)

:: Fallback: try any Ethernet adapter whether up or down
if "!ETH_ADAPTER!"=="" (
    for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-NetAdapter | Where-Object {$_.InterfaceDescription -like '*Ethernet*' -or $_.Name -like '*Ethernet*'} | Select-Object -First 1 -ExpandProperty Name" 2^>nul') do (
        SET ETH_ADAPTER=%%a
    )
)

if "!ETH_ADAPTER!"=="" (
    echo [WARN]   Could not auto-detect Ethernet adapter.
    echo          Please set the static IP manually:
    echo            Adapter: your Ethernet / LAN adapter
    echo            IP:      192.168.125.2
    echo            Subnet:  255.255.255.0
    echo            Gateway: (leave blank)
    echo.
) else (
    echo          Adapter found: !ETH_ADAPTER!
    netsh interface ip set address "!ETH_ADAPTER!" static %IRC5_IP% %IRC5_SUBNET% >nul 2>&1
    if errorlevel 1 (
        echo [WARN]   Could not set IP automatically. You may need to run this
        echo          script as Administrator, or set it manually:
        echo            IP:      192.168.125.2
        echo            Subnet:  255.255.255.0
        echo            Gateway: (leave blank)
    ) else (
        echo [OK]     IP set to %IRC5_IP% on adapter "!ETH_ADAPTER!"
    )
)

echo.
echo ── Step 3: Testing IRC5 connection ────────────────────────
echo.
echo Pinging IRC5 at %IRC5_CONTROLLER%...
ping -n 3 %IRC5_CONTROLLER% >nul 2>&1
if errorlevel 1 (
    echo [WARN]   No response from IRC5 at %IRC5_CONTROLLER%.
    echo          This is fine if the controller is currently off.
    echo          The pipeline will connect automatically when it powers on.
) else (
    echo [OK]     IRC5 is reachable at %IRC5_CONTROLLER%
)

echo.
echo ── Step 4: Setting pipeline to start on login ─────────────
echo.
echo Adding start_pipeline.bat to Windows Task Scheduler...
echo (The pipeline window will open automatically each time you log in.)
echo.

SET TASK_NAME=IRC5 Auto-Deploy Pipeline
SET TASK_CMD="%SCRIPT_DIR%start_pipeline.bat"

:: Remove existing task first to avoid duplicates
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "%TASK_CMD%" ^
    /sc onlogon ^
    /ru "%USERNAME%" ^
    /rl limited ^
    /f >nul 2>&1

if errorlevel 1 (
    echo [WARN]   Task Scheduler setup failed.
    echo          To start the pipeline manually: double-click start_pipeline.bat
    echo          To set it up manually later: run this script as Administrator.
) else (
    echo [OK]     Task scheduled: pipeline starts automatically on login.
    echo          Task name: "%TASK_NAME%"
    echo          To remove: schtasks /delete /tn "%TASK_NAME%" /f
)

echo.
echo ── Done ───────────────────────────────────────────────────
echo.
echo Setup complete. Summary:
echo   Scripts folder : %SCRIPT_DIR%
echo   IRC5 IP target : %IRC5_CONTROLLER%
echo   Laptop IP      : %IRC5_IP%
echo   GDrive inbox   : G:\My Drive\RobotInbox\Inbox
echo.
echo To start the pipeline now: double-click start_pipeline.bat
echo It will also start automatically next time you log in.
echo.
pause
endlocal
