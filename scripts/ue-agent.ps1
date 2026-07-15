param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$UeakArguments
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$EntryPoint = Join-Path $PSScriptRoot "ue-agent.py"

Assert-UeakPath -Path $VenvPython -Description "Project Python environment" -PathType File
Assert-UeakPath -Path $EntryPoint -Description "ue-agent CLI entry point" -PathType File

& $VenvPython $EntryPoint @UeakArguments
exit $LASTEXITCODE
