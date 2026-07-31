@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpLiveWrite.ps1" %*
exit /b %ERRORLEVEL%
