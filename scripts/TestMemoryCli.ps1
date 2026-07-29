$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$TestScript = Join-Path $ToolRoot "tests\integration\memory_cli_smoke.py"
Assert-UeakPath -Path $VenvPython -Description "project Python environment" -PathType File
Assert-UeakPath -Path $TestScript -Description "Project Memory CLI smoke test" -PathType File

& $VenvPython $TestScript
if ($LASTEXITCODE -ne 0)
{
    throw "Project Memory CLI smoke test failed with exit code $LASTEXITCODE"
}
