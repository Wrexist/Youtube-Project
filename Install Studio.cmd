@echo off
rem ===========================================================================
rem  Studio - double-click this once, to install.
rem
rem  This exists because the documented alternative was:
rem
rem    powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
rem
rem  which is a command you have to type correctly, in the right directory, and
rem  whose `-ExecutionPolicy Bypass` looks alarming without the explanation that
rem  Windows refuses to run unsigned scripts by default. Double-clicking
rem  setup.ps1 directly does not work either - Explorer opens .ps1 files in
rem  Notepad. So: a .cmd, which Windows does run on a double-click, that invokes
rem  PowerShell correctly on your behalf.
rem
rem  Afterwards, Studio.cmd starts the app, and a Studio shortcut is placed on
rem  your Desktop.
rem ===========================================================================

cd /d "%~dp0"
title Installing Studio

rem No banner here. setup.ps1 prints its own, and two of them - one from cmd and
rem one from PowerShell, in different styles - was the first thing anyone saw.

powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\setup.ps1"
set SETUP_EXIT=%errorlevel%

if %SETUP_EXIT% neq 0 (
  echo   Setup did not finish. The reason is above.
  echo.
  pause
  exit /b %SETUP_EXIT%
)

rem Nothing restated on success either: setup.ps1's closing block already says
rem what to do next, and repeating it four lines later reads as a second, subtly
rem different instruction. The pause is still needed - a double-clicked window
rem closes the instant this file ends.
pause
