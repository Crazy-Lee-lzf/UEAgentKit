param(
    [string]$Database = "",
    [switch]$EnableWriteTools,
    [switch]$EnableCommitTools,
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Policy = "",
    [string]$RevisionExport = "",
    [string]$WorkRoot = "",
    [string]$BackupRoot = "",
    [ValidateRange(60, 7200)]
    [int]$ProcessTimeoutSeconds = 1800,
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

if ($EnableCommitTools -and !$EnableWriteTools)
{
    throw "EnableCommitTools requires EnableWriteTools."
}

$Arguments = @($EntryPoint, "--database", $Database)
if ($EnableWriteTools)
{
    $EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
    $ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
    if ([string]::IsNullOrWhiteSpace($Policy))
    {
        throw "Policy is required when EnableWriteTools is set."
    }
    if ([string]::IsNullOrWhiteSpace($RevisionExport))
    {
        throw "RevisionExport is required when EnableWriteTools is set."
    }
    $Policy = [System.IO.Path]::GetFullPath($Policy)
    $RevisionExport = [System.IO.Path]::GetFullPath($RevisionExport)
    Assert-UeakPath -Path $Policy -Description "write policy JSON" -PathType File
    Assert-UeakPath -Path $RevisionExport -Description "Revision Export" -PathType Directory
    if ([string]::IsNullOrWhiteSpace($WorkRoot))
    {
        $WorkRoot = Join-Path $ToolRoot "Output\McpWorkflow"
    }
    else
    {
        $WorkRoot = [System.IO.Path]::GetFullPath($WorkRoot)
    }
    if ([string]::IsNullOrWhiteSpace($BackupRoot))
    {
        $BackupRoot = Join-Path $ToolRoot "Backups\McpWorkflow"
    }
    else
    {
        $BackupRoot = [System.IO.Path]::GetFullPath($BackupRoot)
    }
    $Arguments += @(
        "--enable-write-tools",
        "--engine-root", $EngineRoot,
        "--project", $ProjectPath,
        "--policy", $Policy,
        "--revision-export", $RevisionExport,
        "--work-root", $WorkRoot,
        "--backup-root", $BackupRoot,
        "--process-timeout-seconds", [string]$ProcessTimeoutSeconds
    )
    if ($EnableCommitTools)
    {
        $Arguments += "--enable-commit-tools"
    }
}
elseif (
    ![string]::IsNullOrWhiteSpace($EngineRoot) -or
    ![string]::IsNullOrWhiteSpace($ProjectPath) -or
    ![string]::IsNullOrWhiteSpace($Policy) -or
    ![string]::IsNullOrWhiteSpace($RevisionExport) -or
    ![string]::IsNullOrWhiteSpace($WorkRoot) -or
    ![string]::IsNullOrWhiteSpace($BackupRoot)
)
{
    throw "Workflow paths require EnableWriteTools."
}

& $VenvPython -c "import mcp"
if ($LASTEXITCODE -ne 0)
{
    throw "MCP dependency is not installed. Run scripts\setup_python.cmd -WithMcp."
}

if ($Check)
{
    $Arguments += "--check"
}

# Do not write informational text to stdout: stdio is the MCP protocol transport.
& $VenvPython @Arguments
exit $LASTEXITCODE
