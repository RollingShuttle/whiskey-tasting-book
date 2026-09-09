@echo off
REM Whiskey Tasting Book - starts the app and opens it in its own window.
REM Closing that window stops the app; there is nothing else to shut down.
cd /d "%~dp0"

REM Re-run ourselves minimised so the console stays out of the way.
if /i not "%~1"=="-started" (
    start "Whiskey Tasting Book" /min "%~f0" -started
    exit /b
)

python launch.py %*
if errorlevel 1 (
    echo.
    echo The app could not start. The message above says why.
    pause
)
