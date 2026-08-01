@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpLiveClosedLoop.ps1" %*
exit /b %ERRORLEVEL%
