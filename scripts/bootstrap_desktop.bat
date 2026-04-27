@echo off
REM ================================================================
REM  Aesura Dashboard — desktop bootstrap
REM  Run ONCE on a fresh machine after cloning the repo.
REM  Safe to re-run; skips steps that are already complete.
REM ================================================================

setlocal
cd /d "%~dp0.."
set REPO_DIR=%CD%

echo.
echo ======================================================
echo    Aesura Dashboard - Desktop Bootstrap
echo    Repo: %REPO_DIR%
echo ======================================================
echo.

REM ---- Check Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [FAIL] Python not found on PATH.
    echo        Install from https://www.python.org/downloads/
    echo        IMPORTANT: check "Add python.exe to PATH" during install.
    exit /b 1
)
echo [OK]   Python:  & python --version

REM ---- Check Git ----
where git >nul 2>nul
if errorlevel 1 (
    echo [FAIL] Git not found on PATH.
    echo        Install from https://git-scm.com/download/win
    exit /b 1
)
echo [OK]   Git:     & git --version
echo.

REM ---- Step 1: git identity ----
echo Step 1/4: Configuring git identity...
git config --global user.name "aesurahealth"
git config --global user.email "aesurahealth1@gmail.com"
echo         Done.
echo.

REM ---- Step 2: copy credentials from OneDrive ----
echo Step 2/4: Copying credentials from OneDrive...
set ONEDRIVE_CREDS=%USERPROFILE%\OneDrive\Desktop\Claude\Aesura Dashboard\credentials
if exist "credentials\youtube-token.json" (
    echo         Credentials already present. Skipping.
) else if not exist "%ONEDRIVE_CREDS%\youtube-token.json" (
    echo         WARNING: OneDrive credentials not found at:
    echo           %ONEDRIVE_CREDS%
    echo         Wait for OneDrive to finish syncing, then re-run this script.
    echo         Without credentials, build_data.py cannot fetch platform data.
) else (
    xcopy "%ONEDRIVE_CREDS%" "credentials" /E /I /Y /Q >nul
    echo         Copied from OneDrive.
)
echo.

REM ---- Step 3: pip install ----
echo Step 3/4: Installing Python dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
echo.

REM ---- Step 4: copy JaneApp feed if present ----
echo Step 4/4: Syncing JaneApp daily feed from OneDrive (if any)...
set ONEDRIVE_JANE=%USERPROFILE%\OneDrive\Desktop\Claude\Aesura Dashboard\data\janeapp\daily_summary.jsonl
if exist "%ONEDRIVE_JANE%" (
    if not exist "data\janeapp" mkdir "data\janeapp"
    copy /Y "%ONEDRIVE_JANE%" "data\janeapp\daily_summary.jsonl" >nul
    echo         Copied latest daily_summary.jsonl.
) else (
    echo         No daily_summary.jsonl yet; will populate when daily automation runs.
)
echo.

echo ======================================================
echo    Bootstrap complete.
echo ======================================================
echo.
echo Next steps:
echo   1. Test the build:        scripts\daily_refresh.bat
echo      (first push will prompt a browser sign-in to GitHub)
echo   2. Schedule in Task Scheduler for 5:00 AM daily.
echo.

endlocal
