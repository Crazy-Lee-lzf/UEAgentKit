@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ManageEnginePluginLink.ps1" -Action Install %*
exit /b %errorlevel%
