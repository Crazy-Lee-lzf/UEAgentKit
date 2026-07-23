@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpClients.ps1" %*
exit /b %ERRORLEVEL%
