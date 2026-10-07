@echo off
rem Chay tool ma khong can cai dat gi: lk order data\bom_mau.txt
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -m lkorder %*
endlocal
