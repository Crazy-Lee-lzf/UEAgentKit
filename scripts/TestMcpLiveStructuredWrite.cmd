@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpLiveStructuredWrite.ps1" %*
exit /b %ERRORLEVEL%
