@echo off
rem ===========================================================================
rem  Studio - double-click this to start.
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
rem working directory - a shortcut launched from the Desktop otherwise runs with
rem the Desktop as its cwd and nothing resolves.
cd /d "%~dp0"

title Studio

rem Both checks point at one place - "Install Studio.cmd" - rather than at the
rem underlying cause. Whether Node is missing or node_modules is, the next action
rem is identical, and the installer itself explains and offers to fix whichever
rem prerequisite is actually absent.
where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Studio is not set up on this computer yet.
  echo.
  echo   Double-click "Install Studio.cmd" in this folder first.
  echo   It installs everything Studio needs, including Node.js, and takes
  echo   a couple of minutes. You only have to do it once.
  echo.
  pause
  exit /b 1
)

if not exist "node_modules\" (
  echo.
  echo   Studio is not installed yet.
  echo.
  echo   Double-click "Install Studio.cmd" in this folder first.
  echo   It takes a couple of minutes and only has to be done once.
  echo.
  pause
  exit /b 1
)

call npm start -- --open

rem Reached when Studio stops - normally because this window was closed, but
rem also on a crash, and in that case the reason is above and worth reading.
echo.
echo   Studio has stopped.
echo.
pause
