@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpLiveReferenceWrite.ps1" %*
exit /b %ERRORLEVEL%
