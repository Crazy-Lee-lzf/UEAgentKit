@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RunReflectionExport.ps1" %*
