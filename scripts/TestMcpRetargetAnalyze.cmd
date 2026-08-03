@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpRetargetAnalyze.ps1" %*
exit /b %ERRORLEVEL%
