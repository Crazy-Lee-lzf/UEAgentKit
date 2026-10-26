param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Asset = "",
    [string]$Root = "",
    [string]$Output = "",
    [string]$Database = "",
    [ValidateSet("index", "structure", "logic", "defaults", "full", "ai")]
    [string]$Profile = "logic",
    [ValidateSet("json", "bpctx", "both")]
    [string]$Format = "both",
    [string]$Graph = "",
    [switch]$CompactJson,
    [switch]$IncludeLayout,
    [switch]$NoNodeProperties,
    [switch]$IncludeUnchangedDefaults
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
if ([string]::IsNullOrWhiteSpace($Database))
{
    $Database = Join-Path $ToolRoot ".data\ue_agent_kit.sqlite3"
}
$Database = [System.IO.Path]::GetFullPath($Database)


Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File

if ([string]::IsNullOrWhiteSpace($Asset) -eq [string]::IsNullOrWhiteSpace($Root))
{
    throw "Specify exactly one of -Asset or -Root."
}

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output"
}
else
{
    $Output = [System.IO.Path]::GetFullPath($Output)
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$Arguments = @(
    $ProjectPath,
    "-run=BlueprintContextExport",
    "-Output=$Output",
    "-Profile=$Profile",
    "-Format=$Format",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NoSound",
    "-NullRHI",
    "-stdout",
    "-FullStdOutLogOutput"
)

if (![string]::IsNullOrWhiteSpace($Asset))
{
    $Arguments += "-Asset=$Asset"
}
else
{
    $Arguments += "-Root=$Root"
}

if (![string]::IsNullOrWhiteSpace($Graph))
{
    $Arguments += "-Graph=$Graph"
}
if ($CompactJson)
{
    $Arguments += "-CompactJson"
}
if ($IncludeLayout)
{
    $Arguments += "-IncludeLayout"
}
if ($NoNodeProperties)
{
    $Arguments += "-NoNodeProperties"
}
if ($IncludeUnchangedDefaults)
{
    $Arguments += "-IncludeUnchangedDefaults"
}

Write-Host "Running BlueprintContextExport..."
Write-Host "Engine  : $EngineRoot"
Write-Host "Project : $ProjectPath"
Write-Host "Output  : $Output"
Write-Host "Profile : $Profile"

& $EditorCmd @Arguments
$ExportExitCode = $LASTEXITCODE
Write-Host "Index update status (export does not update SQLite):"
Show-UeakIndexUpdateStatus -Database $Database
if ($ExportExitCode -ne 0)
{
    throw "BlueprintContextExport failed with exit code $ExportExitCode"
}

Write-Host "Export completed: $Output"
