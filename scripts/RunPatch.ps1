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
    [string]$BackupDir = "",
    [string]$Manifest = "",
    [string]$TestFailureInjection = ""
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

$AllowedTestFailureInjections = @("", "DirtyPackage", "SaveFailure")
if (!($AllowedTestFailureInjections -contains $TestFailureInjection))
{
    throw "TestFailureInjection must be empty, DirtyPackage, or SaveFailure."
}

if ($Mode -eq "Commit" -and ![string]::IsNullOrWhiteSpace($Manifest))
{
    $Manifest = [System.IO.Path]::GetFullPath($Manifest)
    if ([System.IO.Path]::GetExtension($Manifest) -ne ".json")
    {
        throw "Backup manifest output must use a .json extension: $Manifest"
    }
    $BackupRootPrefix = $BackupDir.TrimEnd([char]'\', [char]'/') + [System.IO.Path]::DirectorySeparatorChar
    if (!$Manifest.StartsWith($BackupRootPrefix, [System.StringComparison]::OrdinalIgnoreCase))
    {
        throw "Backup manifest output must stay inside BackupDir: $Manifest"
    }
    foreach ($ProtectedPath in @($Patch, $Policy, $Report, $ValidationReport))
    {
        if ($Manifest.Equals($ProtectedPath, [System.StringComparison]::OrdinalIgnoreCase))
        {
            throw "Backup manifest output conflicts with another patch input or report: $Manifest"
        }
    }
    if (Test-Path -LiteralPath $Manifest)
    {
        throw "Backup manifest output already exists: $Manifest"
    }
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
    "setMaterialInstanceStaticSwitchParameter",
    "setDataTableCell",
    "setDataTableRowFields"
)
$Commandlet = if ($AssetOperations -contains $Operation) { "AssetPatch" } else { "BlueprintPatch" }
if (![string]::IsNullOrWhiteSpace($TestFailureInjection) -and $Commandlet -ne "AssetPatch")
{
    throw "TestFailureInjection is available only for AssetPatch regression fixtures."
}

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
if (![string]::IsNullOrWhiteSpace($TestFailureInjection))
{
    $Arguments += "-TestFailureInjection=$TestFailureInjection"
}

Write-Host "Running $Commandlet..."
Write-Host "Engine    : $EngineRoot"
Write-Host "Project   : $ProjectPath"
Write-Host "Mode      : $Mode"
Write-Host "Patch     : $Patch"
Write-Host "Policy    : $Policy"
Write-Host "Report    : $Report"
Write-Host "Validation: $ValidationReport"
if (![string]::IsNullOrWhiteSpace($TestFailureInjection))
{
    Write-Host "Test fault : $TestFailureInjection"
}
if ($Mode -eq "Commit")
{
    Write-Host "Backup    : $BackupDir"
}

& $EditorCmd @Arguments
if ($LASTEXITCODE -ne 0)
{
    throw "$Commandlet failed with exit code $LASTEXITCODE"
}

if ($Mode -eq "Commit")
{
    $CommitReport = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
    if (!$CommitReport.saved -or [string]::IsNullOrWhiteSpace([string]$CommitReport.backupPath))
    {
        throw "Commit report does not contain a successful save and backup path."
    }
    if ([string]::IsNullOrWhiteSpace($Manifest))
    {
        $Manifest = "{0}.manifest.json" -f [string]$CommitReport.backupPath
    }
    Write-Host "Creating backup manifest..."
    & $AgentCmd patch manifest `
        --patch $Patch `
        --policy $Policy `
        --report $Report `
        --backup-root $BackupDir `
        --output $Manifest
    if ($LASTEXITCODE -ne 0)
    {
        throw "Backup manifest creation failed with exit code $LASTEXITCODE. The Commit and raw backup remain available."
    }
    Write-Host "Manifest  : $Manifest"
}

Write-Host "Patch completed: $Report"
