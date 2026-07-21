param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [Parameter(Mandatory = $true)]
    [string]$Patch,
    [Parameter(Mandatory = $true)]
    [string]$Policy,
    [Parameter(Mandatory = $true)]
    [string]$RevisionExport,
    [ValidateSet("DryRun", "Commit")]
    [string]$Mode = "DryRun",
    [string]$Report = "",
    [string]$ValidationReport = "",
    [string]$BackupDir = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
$AgentCmd = Join-Path $PSScriptRoot "ue-agent.cmd"

Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File
Assert-UeakPath -Path $AgentCmd -Description "ue-agent.cmd" -PathType File

$Patch = [System.IO.Path]::GetFullPath($Patch)
$Policy = [System.IO.Path]::GetFullPath($Policy)
$RevisionExport = [System.IO.Path]::GetFullPath($RevisionExport)
Assert-UeakPath -Path $Patch -Description "Patch JSON" -PathType File
Assert-UeakPath -Path $Policy -Description "Write policy JSON" -PathType File
Assert-UeakPath -Path $RevisionExport -Description "Revision export" -PathType Directory

if ([string]::IsNullOrWhiteSpace($Report))
{
    $Report = Join-Path $ToolRoot "Output\Patch\patch-report.json"
}
else
{
    $Report = [System.IO.Path]::GetFullPath($Report)
}

if ([string]::IsNullOrWhiteSpace($ValidationReport))
{
    $ValidationReport = Join-Path ([System.IO.Path]::GetDirectoryName($Report)) "validation-report.json"
}
else
{
    $ValidationReport = [System.IO.Path]::GetFullPath($ValidationReport)
}

if ([string]::IsNullOrWhiteSpace($BackupDir))
{
    $BackupDir = Join-Path $ToolRoot "Backups\Patches"
}
else
{
    $BackupDir = [System.IO.Path]::GetFullPath($BackupDir)
}

New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Report)) -Force | Out-Null
New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($ValidationReport)) -Force | Out-Null
if ($Mode -eq "Commit")
{
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

Write-Host "Validating patch..."
& $AgentCmd patch validate `
    --patch $Patch `
    --policy $Policy `
    --export $RevisionExport `
    --report $ValidationReport
if ($LASTEXITCODE -ne 0)
{
    throw "Patch validation failed with exit code $LASTEXITCODE"
}

$Validation = Get-Content -LiteralPath $ValidationReport -Raw | ConvertFrom-Json
if ($Validation.summary.assets -ne 1 -or $Validation.summary.operations -ne 1)
{
    throw "Patch execution currently requires exactly one asset and one operation per execution."
}
if (!$Validation.commitSupported)
{
    throw "The installed validation layer does not report patch executor support."
}

$Operation = $Validation.assets[0].operations[0].operation
$AssetOperations = @(
    "setAssetProperty",
    "setMaterialInstanceScalarParameter",
    "setMaterialInstanceVectorParameter",
    "setMaterialInstanceTextureParameter",
    "setMaterialInstanceStaticSwitchParameter"
)
$Commandlet = if ($AssetOperations -contains $Operation) { "AssetPatch" } else { "BlueprintPatch" }

$Arguments = @(
    $ProjectPath,
    "-run=$Commandlet",
    "-Patch=$Patch",
    "-Policy=$Policy",
    "-Report=$Report",
    "-BackupDir=$BackupDir",
    "-Mode=$Mode",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NoSound",
    "-NullRHI",
    "-stdout",
    "-FullStdOutLogOutput"
)

Write-Host "Running $Commandlet..."
Write-Host "Engine    : $EngineRoot"
Write-Host "Project   : $ProjectPath"
Write-Host "Mode      : $Mode"
Write-Host "Patch     : $Patch"
Write-Host "Policy    : $Policy"
Write-Host "Report    : $Report"
Write-Host "Validation: $ValidationReport"
if ($Mode -eq "Commit")
{
    Write-Host "Backup    : $BackupDir"
}

& $EditorCmd @Arguments
if ($LASTEXITCODE -ne 0)
{
    throw "$Commandlet failed with exit code $LASTEXITCODE"
}

Write-Host "Patch completed: $Report"
