@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0BuildRelease.ps1" %*
exit /b %ERRORLEVEL%
