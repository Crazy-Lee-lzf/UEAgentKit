$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$TestScript = Join-Path $ToolRoot "tests\integration\mcp_stdio_smoke.py"
Assert-UeakPath -Path $VenvPython -Description "project Python environment" -PathType File
Assert-UeakPath -Path $TestScript -Description "MCP stdio smoke test" -PathType File

& $VenvPython -c "import mcp"
if ($LASTEXITCODE -ne 0)
{
    throw "MCP dependency is not installed. Run scripts\setup_python.cmd -WithMcp."
}

& $VenvPython $TestScript
if ($LASTEXITCODE -ne 0)
{
    throw "MCP stdio smoke test failed with exit code $LASTEXITCODE"
}
