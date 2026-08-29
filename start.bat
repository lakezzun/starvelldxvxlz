@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
:run
python main.py
if exist "storage\cache\need_restart" (
  del /f /q "storage\cache\need_restart" >nul 2>&1
  goto run
)
if errorlevel 75 goto run
echo.
pause
