@echo off
setlocal

SET SCRIPT_DIR=%~dp0
SET RCLONE=%SCRIPT_DIR%rclone.exe
SET RCLONE_ZIP=%TEMP%\rclone_download.zip
SET RCLONE_TEMP=%TEMP%\rclone_extract

echo.
echo ============================================================
echo  IRC5 Pipeline - Google Drive Setup
echo ============================================================
echo.

:: ── Check if rclone already exists ────────────────────────────────────────
if exist "%RCLONE%" (
    echo rclone.exe already found. Skipping download.
    goto :configure
)

:: ── Download rclone ───────────────────────────────────────────────────────
echo Downloading rclone...
powershell -NoProfile -Command ^
    "Invoke-WebRequest -Uri 'https://downloads.rclone.org/rclone-current-windows-amd64.zip' -OutFile '%RCLONE_ZIP%' -UseBasicParsing"
if errorlevel 1 (
    echo.
    echo ERROR: Download failed. Check your internet connection and try again.
    pause
    exit /b 1
)

echo Extracting rclone...
if exist "%RCLONE_TEMP%" rmdir /s /q "%RCLONE_TEMP%"
powershell -NoProfile -Command ^
    "Expand-Archive -Path '%RCLONE_ZIP%' -DestinationPath '%RCLONE_TEMP%' -Force"
if errorlevel 1 (
    echo ERROR: Extraction failed.
    pause
    exit /b 1
)

:: Copy rclone.exe from the extracted subfolder to the script directory
powershell -NoProfile -Command ^
    "Get-ChildItem -Path '%RCLONE_TEMP%' -Recurse -Filter 'rclone.exe' | Select-Object -First 1 | Copy-Item -Destination '%RCLONE%'"
if not exist "%RCLONE%" (
    echo ERROR: Could not find rclone.exe in the downloaded archive.
    pause
    exit /b 1
)

:: Clean up temp files
del /q "%RCLONE_ZIP%" 2>nul
rmdir /s /q "%RCLONE_TEMP%" 2>nul

echo rclone downloaded successfully.
echo.

:: ── Configure Google Drive ────────────────────────────────────────────────
:configure
echo ============================================================
echo  Connecting to Google Drive
echo ============================================================
echo.
echo This will open your browser to log in to Google Drive.
echo When prompted, sign in with the Google account that has
echo access to the shared Robot inbox folder.
echo.
echo IMPORTANT: When rclone asks for a remote name, type exactly:
echo   gdrive
echo.
echo Then choose Google Drive from the list (type the number),
echo and follow the prompts. For all other questions, press Enter
echo to accept the defaults.
echo.
pause

"%RCLONE%" config

:: ── Verify the connection works ───────────────────────────────────────────
echo.
echo Verifying Google Drive connection...
"%RCLONE%" lsd gdrive: 2>nul
if errorlevel 1 (
    echo.
    echo WARNING: Could not list Google Drive contents.
    echo Make sure you named the remote exactly 'gdrive' and authorised access.
    echo You can run this setup again to reconfigure.
    pause
    exit /b 1
)

:: ── Create local folders ──────────────────────────────────────────────────
echo.
echo Creating local folders...
mkdir "%SCRIPT_DIR%inbox"      2>nul
mkdir "%SCRIPT_DIR%processed"  2>nul
mkdir "%SCRIPT_DIR%split_output" 2>nul
echo Done.

:: ── Final instructions ────────────────────────────────────────────────────
echo.
echo ============================================================
echo  Setup complete!
echo ============================================================
echo.
echo NEXT STEPS:
echo.
echo 1. In your Google Drive, create this folder structure:
echo      My Drive /
echo        RobotInbox /
echo          Inbox /        ^<-- programmer drops files here
echo          Processed /    ^<-- pipeline archives files here
echo.
echo 2. Share the RobotInbox folder with your programmer.
echo.
echo 3. Start the pipeline: double-click start_pipeline.bat
echo.
echo PROGRAMMER NAMING RULES:
echo   Use ONLY letters, numbers, and underscores in file names.
echo   Example: GondorRand_100mm.pgf  (GOOD)
echo            Gondor-Rand 100mm.pgf (BAD - dashes and spaces break ABB RAPID)
echo.
echo   Upload passes in execution order (biggest tool first).
echo   The pipeline processes them in the order they are uploaded.
echo.
pause
endlocal
