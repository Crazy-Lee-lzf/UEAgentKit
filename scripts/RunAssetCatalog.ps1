param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Asset = "",
    [string]$Root = "",
    [string]$Output = "",
    [switch]$IncludeBlueprints,
    [switch]$IncludeGenerated,
    [switch]$NoTags,
    [switch]$CompactJson
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"

Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File

if ([string]::IsNullOrWhiteSpace($Asset) -eq [string]::IsNullOrWhiteSpace($Root))
{
    throw "Specify exactly one of -Asset or -Root."
}

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output\AssetCatalog"
}
else
{
    $Output = [System.IO.Path]::GetFullPath($Output)
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$Arguments = @(
    $ProjectPath,
    "-run=AssetCatalogExport",
    "-Output=$Output",
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
if ($IncludeBlueprints)
{
    $Arguments += "-IncludeBlueprints"
}
if ($IncludeGenerated)
{
    $Arguments += "-IncludeGenerated"
}
if ($NoTags)
{
    $Arguments += "-NoTags"
}
if ($CompactJson)
{
    $Arguments += "-CompactJson"
}

Write-Host "Running AssetCatalogExport..."
Write-Host "Engine  : $EngineRoot"
Write-Host "Project : $ProjectPath"
Write-Host "Output  : $Output"

& $EditorCmd @Arguments
if ($LASTEXITCODE -ne 0)
{
    throw "AssetCatalogExport failed with exit code $LASTEXITCODE"
}

Write-Host "Asset catalog export completed: $Output"
