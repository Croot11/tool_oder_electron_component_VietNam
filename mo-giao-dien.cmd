@echo off
rem Nhay doi vao file nay de mo giao dien tren trinh duyet.
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -m lkorder web
if errorlevel 1 (
  echo.
  echo Khong chay duoc. Kiem tra da cai Python chua: python --version
  pause
)
endlocal
