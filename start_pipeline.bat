@echo off
title IRC5 Auto-Deploy Pipeline
cd /d "%~dp0"

echo.
echo ============================================================
echo  IRC5 Auto-Deploy Pipeline
echo  Close this window to stop. Do not close while uploading.
echo ============================================================
echo.

:loop
python pipeline_service.py
echo.
echo --- Pipeline stopped. Restarting in 5 seconds ---
echo --- Press Ctrl+C to exit ---
echo.
timeout /t 5 /nobreak >nul
goto loop
