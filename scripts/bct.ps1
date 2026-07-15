param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BctArguments
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-BctToolRoot
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$EntryPoint = Join-Path $PSScriptRoot "bct.py"

Assert-BctPath -Path $VenvPython -Description "Project Python environment" -PathType File
Assert-BctPath -Path $EntryPoint -Description "BCT CLI entry point" -PathType File

& $VenvPython $EntryPoint @BctArguments
exit $LASTEXITCODE
