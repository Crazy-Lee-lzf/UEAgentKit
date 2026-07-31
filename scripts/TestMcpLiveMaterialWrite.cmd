@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpLiveMaterialWrite.ps1" %*
exit /b %ERRORLEVEL%
