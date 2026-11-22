@echo off
rem Spusteni aplikace Metex M-3650D. Pri prvnim spusteni pripravi prostredi.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Pripravuji prostredi Pythonu ^(jen poprve, chvili to trva^)...
  py -3 -m venv .venv || python -m venv .venv || goto :err
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
)
start "" ".venv\Scripts\pythonw.exe" -m metex_dmm %*
exit /b 0
:err
echo.
echo Instalace selhala. Je nainstalovany Python 3.11 nebo novejsi? Viz README.md.
pause
exit /b 1
