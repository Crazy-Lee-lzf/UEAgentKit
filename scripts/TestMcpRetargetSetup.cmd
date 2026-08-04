@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpRetargetSetup.ps1" %*
exit /b %ERRORLEVEL%
