@echo off
setlocal
set "PYTHON_EXE=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo Project Python environment not found. Run scripts\setup_python.cmd first. 1>&2
    exit /b 1
)
"%PYTHON_EXE%" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
