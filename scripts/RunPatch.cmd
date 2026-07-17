@echo off

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RunPatch.ps1" %*

exit /b %ERRORLEVEL%
