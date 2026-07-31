@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpLiveWriteRegression.ps1" %*
exit /b %ERRORLEVEL%
