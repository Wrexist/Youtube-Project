@echo off
rem ===========================================================================
rem  Studio — double-click this to start.
rem
rem  Deliberately at the repository root, so it is the first thing anyone sees,
rem  and deliberately a .cmd rather than something cleverer. A VBScript wrapper
rem  could hide this window entirely, but then a failure is invisible and there
rem  is no obvious way to stop the app; Windows also increasingly blocks .vbs.
rem  A console window that says what it is doing, and that you close to quit, is
rem  the honest version.
rem
rem  It runs `npm start -- --open`, which starts both halves and opens your
rem  browser once the app actually answers. Double-clicking it while Studio is
rem  already running just brings the browser back.
rem ===========================================================================

rem Work from the folder this file lives in, not from wherever Explorer left the
rem working directory — a shortcut launched from the Desktop otherwise runs with
rem the Desktop as its cwd and nothing resolves.
cd /d "%~dp0"

title Studio

where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Node.js is not installed, or is not on your PATH.
  echo.
  echo   Studio needs it to run. Install the LTS version from:
  echo     https://nodejs.org
  echo.
  echo   Then double-click this file again.
  echo.
  pause
  exit /b 1
)

if not exist "node_modules\" (
  echo.
  echo   Studio is not installed yet.
  echo.
  echo   Right-click Setup.ps1 in this folder and choose "Run with PowerShell",
  echo   or run this in PowerShell from this folder:
  echo.
  echo     powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
  echo.
  echo   It takes a couple of minutes and only has to be done once.
  echo.
  pause
  exit /b 1
)

call npm start -- --open

rem Reached when Studio stops — normally because this window was closed, but
rem also on a crash, and in that case the reason is above and worth reading.
echo.
echo   Studio has stopped.
echo.
pause
