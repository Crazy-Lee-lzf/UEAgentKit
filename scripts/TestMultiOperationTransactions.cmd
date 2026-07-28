@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0TestMultiOperationTransactions.ps1" %*
exit /b %ERRORLEVEL%
