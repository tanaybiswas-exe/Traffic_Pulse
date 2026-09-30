@echo off
:loop
echo Checking for changes...
git add .
git commit -m "Auto sync: %date% %time%"
git push origin main
echo Done. Waiting 30 seconds for next sync...
timeout /t 30 /nobreak >nul
goto loop