@echo off
cd /d "%~dp0"
:run
python main.py
if exist "storage\cache\need_restart" (
  del /f /q "storage\cache\need_restart" >nul 2>&1
  goto run
)
if errorlevel 75 goto run
echo.
pause
