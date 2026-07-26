@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestDataAssetStructuredProperties.ps1" %*
exit /b %ERRORLEVEL%
