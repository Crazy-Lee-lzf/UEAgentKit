@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0CreateAgentWorktrees.ps1" %*
exit /b %ERRORLEVEL%
