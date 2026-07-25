@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestDataAssetReferenceProperties.ps1" %*
exit /b %ERRORLEVEL%
