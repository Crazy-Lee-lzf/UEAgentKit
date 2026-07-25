@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestDataTableRowOperations.ps1" %*
exit /b %ERRORLEVEL%
