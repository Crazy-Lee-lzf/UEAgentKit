@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMcpLiveUndoDiscard.ps1" %*
exit /b %ERRORLEVEL%
