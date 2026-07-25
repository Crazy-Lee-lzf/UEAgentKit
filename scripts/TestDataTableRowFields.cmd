@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestDataTableRowFields.ps1" %*
exit /b %ERRORLEVEL%
