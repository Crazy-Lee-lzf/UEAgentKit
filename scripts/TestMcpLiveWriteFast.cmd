@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpLiveWriteFast.ps1" %*
exit /b %ERRORLEVEL%
