@echo off
REM ================================================================
REM  Aesura Dashboard — daily refresh
REM  Runs build_data.py, then commits + pushes changes to GitHub.
REM  Scheduled via Windows Task Scheduler (see Phase 2 setup).
REM
REM  Logs append to scripts/daily_refresh.log so you can see what
REM  happened if a run failed while you were away from the computer.
REM ================================================================

cd /d "C:\Users\nn214\OneDrive\Desktop\Claude\Aesura Dashboard"

echo === Aesura Dashboard refresh — %DATE% %TIME% ===

echo.
echo [1/2] Running build_data.py (this takes 2-5 minutes)...
python build_data.py

echo.
echo [2/2] Pushing changes to GitHub...
git add -A
git commit -m "Daily dashboard refresh" 2>nul
git push origin main

echo.
echo === Done — %DATE% %TIME% ===
