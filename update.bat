@echo off
REM Whiskey Tasting Book - bring this computer up to date.
REM Fetches the latest code, reinstalls anything new it needs, and rebuilds the app.
REM Safe to run any time: it stops early and says so when there is nothing to do.
cd /d "%~dp0"

python update.py
if errorlevel 1 (
    echo.
    echo The update did not finish. The message above says why.
)
echo.
pause
