@echo off
rem Nhay doi vao file nay de mo giao dien tren trinh duyet.
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

python --version >nul 2>&1
if errorlevel 1 (
  echo Chua cai Python ^(can 3.10 tro len^). Tai tai https://www.python.org/downloads/
  echo Khi cai nho tick "Add python.exe to PATH".
  pause
  exit /b 1
)

rem Playwright chi can cho buoc "Bat dau" (mo Chrome, bo hang vao gio).
python -c "import playwright" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [!] Chua cai Playwright - van mo duoc giao dien, nhung nut "Bat dau"
  echo     ^(bo hang vao gio^) se khong chay.
  echo     Cai bang 2 lenh:
  echo         python -m pip install playwright
  echo         python -m playwright install chrome
  echo.
  choice /c YN /n /m "Cai ngay bay gio? [Y/N] "
  if not errorlevel 2 (
    python -m pip install playwright && python -m playwright install chrome
    if errorlevel 1 (
      echo Cai khong thanh cong. Thu chay lai 2 lenh tren bang tay.
      pause
    )
  )
  echo.
)

python -m lkorder web
if errorlevel 1 (
  echo.
  echo Khong chay duoc giao dien. Xem loi o tren.
  pause
)
endlocal
