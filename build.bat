@echo off
REM One-click build for Windows:  double-click build.bat
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt || goto :err
python scripts\build.py || goto :err
echo.
echo ==== Done!  ->  dist\VidShrink.exe ====
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
