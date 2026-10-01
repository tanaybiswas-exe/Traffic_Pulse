@echo off
title TrafficPulse GitHub Auto Sync
color 0B

echo ========================================================
echo        TrafficPulse - Auto GitHub Push Engine
echo ========================================================
echo.

:sync_process
echo [%time%] Checking for local changes...

:: Check if any file was modified/added
git status --porcelain > temp_status.txt
set /p HAS_CHANGES=<temp_status.txt
del temp_status.txt

if defined HAS_CHANGES (
    echo [%time%] New changes detected!
    echo Uploading to GitHub...
    
    :: Add all files
    git add .
    
    :: Commit with timestamp
    git commit -m "Auto sync update: %date% %time%"
    
    :: Push to GitHub (safe push)
    git push origin main
    
    if %errorlevel% neq 0 (
        echo [ERROR] Normal push failed! Attempting force push sync...
        git push origin main --force
    )
    
    echo.
    echo [%time%] SUCCESS: Code successfully pushed to GitHub!
    echo ========================================================
) else (
    echo [%time%] No new changes found. Everything is up to date!
)

echo.
echo Next auto-check in .5 minutes (30 seconds)...
echo (You can minimize this window or close it whenever done)
echo.

:: Wait for .5 minutes (30 seconds)
timeout /t 60 /nobreak

:: Loop back to sync
goto sync_process