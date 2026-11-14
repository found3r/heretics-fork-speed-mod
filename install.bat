@echo off
setlocal
cd /d "%~dp0"
call :find_python || goto :nopython
%PY% hf_speedmod.py install %*
echo.
pause
exit /b

:find_python
for %%P in ("py -3" "python" "python3") do (
    %%~P -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && (set "PY=%%~P" & exit /b 0)
)
exit /b 1

:nopython
echo This mod needs Python 3.8 or newer (free): https://www.python.org/downloads/
echo During setup, tick "Add python.exe to PATH". Then run this file again.
echo.
pause
exit /b 1
