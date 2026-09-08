@echo off
REM Whiskey Tasting Book — start the local app at http://127.0.0.1:8765
REM Uses config.yaml for the journal folder, snapshot and port. Reads the master only on Refresh.
cd /d "%~dp0"
python app.py %*
pause
