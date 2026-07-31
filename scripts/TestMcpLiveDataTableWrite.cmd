@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpLiveDataTableWrite.ps1" %*
exit /b %ERRORLEVEL%
