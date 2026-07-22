@echo off

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RunScalarPatchRegression.ps1" %*

exit /b %ERRORLEVEL%
