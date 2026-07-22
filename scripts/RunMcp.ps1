param(
    [string]$Database = "",
    [switch]$Check
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$EntryPoint = Join-Path $PSScriptRoot "ue-agent-mcp.py"
if ([string]::IsNullOrWhiteSpace($Database))
{
    $Database = Join-Path $ToolRoot ".data\ue_agent_kit.sqlite3"
}
$Database = [System.IO.Path]::GetFullPath($Database)

Assert-UeakPath -Path $VenvPython -Description "project Python environment" -PathType File
Assert-UeakPath -Path $EntryPoint -Description "MCP entry point" -PathType File
Assert-UeakPath -Path $Database -Description "UE Agent Kit SQLite index" -PathType File

& $VenvPython -c "import mcp"
if ($LASTEXITCODE -ne 0)
{
    throw "MCP dependency is not installed. Run scripts\setup_python.cmd -WithMcp."
}

$Arguments = @($EntryPoint, "--database", $Database)
if ($Check)
{
    $Arguments += "--check"
}

# Do not write informational text to stdout: stdio is the MCP protocol transport.
& $VenvPython @Arguments
exit $LASTEXITCODE
