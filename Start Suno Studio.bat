@echo off
setlocal
cd /d "%~dp0"

if not exist "suno_studio.py" goto missing_app

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 9))"
    if errorlevel 1 goto old_python
    ".venv\Scripts\python.exe" suno_studio.py
    goto done
)

where py >nul 2>&1
if not errorlevel 1 goto use_py
where python >nul 2>&1
if errorlevel 1 goto missing_python

python -c "import sys; raise SystemExit(sys.version_info < (3, 9))"
if errorlevel 1 goto old_python
python suno_studio.py
goto done

:use_py
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 9))"
if errorlevel 1 goto old_python
py -3 suno_studio.py
goto done

:missing_app
echo Could not find suno_studio.py. Keep this launcher in the Suno Studio folder.
goto fail

:missing_python
echo Python 3.9 or later is required. Install Python, then try again.
goto fail

:old_python
echo Python 3.9 or later is required. Install a newer Python, then try again.
goto fail

:fail
set "RESULT=1"
goto pause

:done
set "RESULT=%ERRORLEVEL%"

:pause
echo.
pause
exit /b %RESULT%
