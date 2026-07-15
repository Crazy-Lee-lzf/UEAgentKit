param(
    [string]$ProjectPath = "",
    [string]$PluginName = "BlueprintContextTool",
    [ValidateSet("true", "false")]
    [string]$Enabled = "true",
    [string]$BackupRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-BctToolRoot
$ProjectPath = Resolve-BctProjectPath -ProjectPath $ProjectPath
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$UpdaterScript = Join-Path $PSScriptRoot "UpdateProjectPlugin.py"

Assert-BctPath -Path $VenvPython -Description "Project Python environment" -PathType File
Assert-BctPath -Path $UpdaterScript -Description "Project plugin updater" -PathType File

$Arguments = @(
    $UpdaterScript,
    "--project", $ProjectPath,
    "--plugin", $PluginName,
    "--enabled", $Enabled
)

if (![string]::IsNullOrWhiteSpace($BackupRoot))
{
    $Arguments += @("--backup-root", [System.IO.Path]::GetFullPath($BackupRoot))
}

& $VenvPython @Arguments
if ($LASTEXITCODE -ne 0)
{
    throw "Project plugin update failed with exit code $LASTEXITCODE"
}
