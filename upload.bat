@echo off
:loop
echo Checking for changes...
git add .
git commit -m "Auto sync: %date% %time%"
git push origin main
echo Done. Waiting 300 seconds for next sync...
timeout /t 5 /nobreak >nul
goto loop