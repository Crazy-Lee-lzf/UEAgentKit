@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpModules.ps1" %*
exit /b %ERRORLEVEL%
