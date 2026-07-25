@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestDataTableRowReferenceImpact.ps1" %*
exit /b %ERRORLEVEL%
