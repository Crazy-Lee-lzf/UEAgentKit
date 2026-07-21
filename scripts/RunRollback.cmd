@echo off

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RunRollback.ps1" %*

exit /b %ERRORLEVEL%
