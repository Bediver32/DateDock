@echo off
setlocal
set "APP=%~dp0photo_import.py"
if not exist "%APP%" goto missing_app

if exist "%SystemDrive%\Python314\pythonw.exe" (
    start "" "%SystemDrive%\Python314\pythonw.exe" "%APP%"
    exit /b
)
where pyw.exe >nul 2>&1
if not errorlevel 1 (
    start "" pyw.exe -3 "%APP%"
    exit /b
)
where pythonw.exe >nul 2>&1
if not errorlevel 1 (
    start "" pythonw.exe "%APP%"
    exit /b
)

echo Python 3 was not found. Install Python for Windows with Tkinter and the Python launcher.
pause
exit /b 1

:missing_app
echo photo_import.py was not found. Keep DateDock.cmd next to photo_import.py.
pause
exit /b 1
