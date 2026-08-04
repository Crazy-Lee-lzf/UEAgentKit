@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpAnimationRetargetBatch.ps1" %*
exit /b %ERRORLEVEL%
