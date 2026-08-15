param(
    [string]$Database = "",
    [switch]$EnableProjectMemory,
    [string]$MemoryDatabase = "",
    [switch]$EnableWriteTools,
    [switch]$EnableCommitTools,
    [switch]$EnableLiveEditor,
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Policy = "",
    [string]$PolicyProfile = "",
    [string]$RevisionExport = "",
    [string]$WorkRoot = "",
    [string]$BackupRoot = "",
    [ValidateRange(60, 7200)]
    [int]$ProcessTimeoutSeconds = 1800,
    [ValidateRange(0.1, 600.0)]
    [double]$LiveEditorTimeoutSeconds = 2.0,
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
if ($EnableProjectMemory)
{
    if ([string]::IsNullOrWhiteSpace($MemoryDatabase))
    {
        $MemoryDatabase = Join-Path $ToolRoot ".data\ue_agent_kit_memory.sqlite3"
    }
    $MemoryDatabase = [System.IO.Path]::GetFullPath($MemoryDatabase)
    $Arguments += @(
        "--enable-project-memory",
        "--memory-database", $MemoryDatabase
    )
}
elseif (![string]::IsNullOrWhiteSpace($MemoryDatabase))
{
    throw "MemoryDatabase requires EnableProjectMemory."
}
if ($EnableWriteTools -or $EnableLiveEditor)
{
    $ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
}
if ($EnableWriteTools)
{
    $EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
    if ([string]::IsNullOrWhiteSpace($RevisionExport))
    {
        throw "RevisionExport is required when EnableWriteTools is set."
    }
    $RevisionExport = [System.IO.Path]::GetFullPath($RevisionExport)
    Assert-UeakPath -Path $RevisionExport -Description "Revision Export" -PathType Directory
    if (![string]::IsNullOrWhiteSpace($Policy))
    {
        $Policy = [System.IO.Path]::GetFullPath($Policy)
        Assert-UeakPath -Path $Policy -Description "write policy JSON" -PathType File
    }
    # When -Policy is omitted, ue-agent-mcp.py resolves a project-level Policy
    # from --project + --policy-profile. It reports a clear error if the project
    # has no project-level Policy mapping.
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
        "--revision-export", $RevisionExport,
        "--work-root", $WorkRoot,
        "--backup-root", $BackupRoot,
        "--process-timeout-seconds", [string]$ProcessTimeoutSeconds
    )
    if (![string]::IsNullOrWhiteSpace($Policy))
    {
        $Arguments += @("--policy", $Policy)
    }
    if (![string]::IsNullOrWhiteSpace($PolicyProfile))
    {
        $Arguments += @("--policy-profile", $PolicyProfile)
    }
    if ($EnableCommitTools)
    {
        $Arguments += "--enable-commit-tools"
    }
}

if ($EnableLiveEditor)
{
    $Arguments += @(
        "--enable-live-editor",
        "--live-editor-timeout-seconds", [string]$LiveEditorTimeoutSeconds
    )
    if (!$EnableWriteTools)
    {
        $Arguments += @("--project", $ProjectPath)
    }
}

if (
    !$EnableWriteTools -and (
        ![string]::IsNullOrWhiteSpace($EngineRoot) -or
        ![string]::IsNullOrWhiteSpace($Policy) -or
        ![string]::IsNullOrWhiteSpace($PolicyProfile) -or
        ![string]::IsNullOrWhiteSpace($RevisionExport) -or
        ![string]::IsNullOrWhiteSpace($WorkRoot) -or
        ![string]::IsNullOrWhiteSpace($BackupRoot)
    )
)
{
    throw "Engine, Policy, PolicyProfile, RevisionExport, WorkRoot, and BackupRoot require EnableWriteTools."
}
if (!$EnableWriteTools -and !$EnableLiveEditor -and ![string]::IsNullOrWhiteSpace($ProjectPath))
{
    throw "ProjectPath requires EnableWriteTools or EnableLiveEditor."
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
