@echo off
setlocal
pushd "%~dp0" || exit /b 1

where cl.exe >nul 2>&1
if not errorlevel 1 goto compile

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto missing
set "VSINSTALL="
for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%I"
if not defined VSINSTALL goto missing
call "%VSINSTALL%\Common7\Tools\VsDevCmd.bat" -arch=x64
if errorlevel 1 exit /b 1

:compile
cl /nologo /std:c++17 /EHsc /utf-8 /Zi /Od /MT /Fe:photo_import.exe photo_import.cpp user32.lib gdi32.lib shell32.lib /link /SUBSYSTEM:WINDOWS /DEBUG
set "BUILD_RESULT=%errorlevel%"
popd
exit /b %BUILD_RESULT%

:missing
echo Install Visual Studio Build Tools with Desktop development with C++ and a Windows SDK.
exit /b 1
