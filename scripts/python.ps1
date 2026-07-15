param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PythonArguments
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$VenvPython = Join-Path (Get-UeakToolRoot) ".venv\Scripts\python.exe"
if (!(Test-Path -LiteralPath $VenvPython -PathType Leaf))
{
    throw "Project Python environment not found. Run scripts\setup_python.cmd first."
}

& $VenvPython @PythonArguments
exit $LASTEXITCODE
