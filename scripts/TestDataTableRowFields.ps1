param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$RunAssetCatalog = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$RunPatch = Join-Path $PSScriptRoot "RunPatch.ps1"
$RunRollback = Join-Path $PSScriptRoot "RunRollback.ps1"
foreach ($Required in @($RunAssetCatalog, $RunPatch, $RunRollback))
{
    Assert-UeakPath -Path $Required -Description ([System.IO.Path]::GetFileName($Required)) -PathType File
}

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output\DataTableRowFields054"
}
else
{
    $Output = [System.IO.Path]::GetFullPath($Output)
}
$SafeOutputRoot = [System.IO.Path]::GetFullPath((Join-Path $ToolRoot "Output"))
$SafeOutputPrefix = $SafeOutputRoot.TrimEnd([char]'\', [char]'/') + [System.IO.Path]::DirectorySeparatorChar
if (!$Output.StartsWith($SafeOutputPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
    $Output.Equals($SafeOutputRoot, [System.StringComparison]::OrdinalIgnoreCase))
{
    throw "Output must be a child directory below the tool Output directory: $Output"
}
if (Test-Path -LiteralPath $Output)
{
    $Reparse = Get-ChildItem -LiteralPath $Output -Force -Recurse -ErrorAction Stop |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 } |
        Select-Object -First 1
    if ($null -ne $Reparse)
    {
        throw "Output contains a Junction or symbolic link: $($Reparse.FullName)"
    }
    Remove-Item -LiteralPath $Output -Recurse -Force
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$AssetPackage = "/Game/UEAgentKitWriteTests/DT_CellPatchTarget"
$AssetPath = "$AssetPackage.DT_CellPatchTarget"
$AssetClass = "/Script/Engine.DataTable"
$RowStruct = "/Script/GameplayTags.GameplayTagTableRow"
$RowName = "Row_Alpha"
$ProjectDirectory = Split-Path -Parent $ProjectPath
$PackageFile = Join-Path $ProjectDirectory "Content\UEAgentKitWriteTests\DT_CellPatchTarget.uasset"
Assert-UeakPath -Path $PackageFile -Description "DataTable fixture package" -PathType File

$InitialExport = Join-Path $Output "Initial"
$DryRunExport = Join-Path $Output "DryRunReload"
$CommitExport = Join-Path $Output "CommitReload"
$RollbackExport = Join-Path $Output "RollbackReload"
$PatchPath = Join-Path $Output "patch.json"
$PolicyPath = Join-Path $Output "policy.json"
$DryRunReport = Join-Path $Output "dry-run-report.json"
$DryRunValidation = Join-Path $Output "dry-run-validation.json"
$CommitReport = Join-Path $Output "commit-report.json"
$CommitValidation = Join-Path $Output "commit-validation.json"
$BackupRoot = Join-Path $Output "Backups"
$Manifest = Join-Path $BackupRoot "data-table-row-fields.manifest.json"
$RollbackDryReport = Join-Path $Output "rollback-dry-run.json"
$RollbackCommitReport = Join-Path $Output "rollback-commit.json"
$RollbackVerification = Join-Path $Output "rollback-verification.json"
$EmergencyBackup = Join-Path $Output "baseline-package.uasset"
Copy-Item -LiteralPath $PackageFile -Destination $EmergencyBackup -Force
$Restored = $false

function Write-Utf8Json
{
    param([string]$Path, [object]$Value)
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    $Json = $Value | ConvertTo-Json -Depth 30
    [System.IO.File]::WriteAllText($Path, $Json + "`r`n", [System.Text.UTF8Encoding]::new($false))
}

function Read-CanonicalAsset
{
    param([string]$ExportRoot)
    $Candidates = @(Get-ChildItem -LiteralPath (Join-Path $ExportRoot "canonical") -Filter "*.json" -File -Recurse)
    foreach ($Candidate in $Candidates)
    {
        $Value = [System.IO.File]::ReadAllText($Candidate.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        if ([string]$Value.assetPath -eq $AssetPath)
        {
            return $Value
        }
    }
    throw "Canonical export does not contain $AssetPath under $ExportRoot"
}

function Read-FixtureRow
{
    param([object]$Canonical)
    $Rows = @($Canonical.assetDetails.rows)
    $Row = $Rows | Where-Object { [string]$_.Name -eq $RowName } | Select-Object -First 1
    if ($null -eq $Row)
    {
        throw "DataTable export does not contain row $RowName"
    }
    return $Row
}

try
{
    & $RunAssetCatalog -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Asset $AssetPackage -Output $InitialExport
    if ($LASTEXITCODE -ne 0)
    {
        throw "Initial DataTable export failed with exit code $LASTEXITCODE"
    }
    $InitialCanonical = Read-CanonicalAsset -ExportRoot $InitialExport
    $InitialRow = Read-FixtureRow -Canonical $InitialCanonical
    if ([string]$InitialCanonical.assetClass -ne $AssetClass -or
        [string]$InitialCanonical.assetDetails.rowStructPath -ne $RowStruct)
    {
        throw "Unexpected DataTable fixture class or RowStruct."
    }
    $InitialRevision = [string]$InitialCanonical.revision.value
    $InitialTag = [string]$InitialRow.Tag
    $InitialComment = [string]$InitialRow.DevComment
    if ([string]::IsNullOrWhiteSpace($InitialRevision))
    {
        throw "Initial DataTable Revision is unavailable."
    }

    $NewTag = if ($InitialTag -ne "UEAgentKit.Atomic.Row") { "UEAgentKit.Atomic.Row" } else { "UEAgentKit.Atomic.Row.Alternate" }
    $NewComment = if ($InitialComment -ne "AtomicRowFields054") { "AtomicRowFields054" } else { "AtomicRowFields054Alternate" }

    $Policy = [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @([System.IO.Path]::GetFileNameWithoutExtension($ProjectPath))
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests")
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @("setDataTableRowFields")
        allowedAssetClasses = @($AssetClass)
        allowedAssetProperties = @()
        allowedMaterialParameters = @()
        allowedDataTableFields = @(
            "$AssetClass#$RowStruct#Tag",
            "$AssetClass#$RowStruct#DevComment"
        )
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    }
    Write-Utf8Json -Path $PolicyPath -Value $Policy

    $Patch = [ordered]@{
        schemaVersion = "1.0"
        patchId = "data-table-row-fields-054"
        projectName = [System.IO.Path]::GetFileNameWithoutExtension($ProjectPath)
        description = "Atomic two-field DataTable row regression"
        assets = @(
            [ordered]@{
                assetPath = $AssetPath
                expectedRevision = $InitialRevision
                expectedAssetClass = $AssetClass
                operations = @(
                    [ordered]@{
                        operationId = "update-row-fields"
                        operation = "setDataTableRowFields"
                        target = [ordered]@{ rowName = $RowName }
                        value = [ordered]@{
                            Tag = $NewTag
                            DevComment = $NewComment
                        }
                    }
                )
            }
        )
    }
    Write-Utf8Json -Path $PatchPath -Value $Patch

    & $RunPatch `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Patch $PatchPath `
        -Policy $PolicyPath `
        -RevisionExport $InitialExport `
        -Mode DryRun `
        -Report $DryRunReport `
        -ValidationReport $DryRunValidation `
        -BackupDir $BackupRoot
    if ($LASTEXITCODE -ne 0)
    {
        throw "DataTable row-fields Dry Run failed with exit code $LASTEXITCODE"
    }
    $Dry = [System.IO.File]::ReadAllText($DryRunReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ([string]$Dry.operation -ne "setDataTableRowFields" -or
        [int]$Dry.fieldCount -ne 2 -or
        $Dry.saved -or
        !$Dry.rolledBack -or
        !$Dry.appliedValueMatch -or
        !$Dry.appliedStructureMatch -or
        !$Dry.rollbackValueMatch -or
        !$Dry.rollbackStructureMatch -or
        !$Dry.diskUnchanged -or
        [string]$Dry.beforeRevision -ne [string]$Dry.afterRevision)
    {
        throw "DataTable row-fields Dry Run report failed its atomicity gates."
    }

    & $RunAssetCatalog -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Asset $AssetPackage -Output $DryRunExport
    if ($LASTEXITCODE -ne 0)
    {
        throw "Dry Run reload export failed with exit code $LASTEXITCODE"
    }
    $DryCanonical = Read-CanonicalAsset -ExportRoot $DryRunExport
    $DryRow = Read-FixtureRow -Canonical $DryCanonical
    if ([string]$DryCanonical.revision.value -ne $InitialRevision -or
        [string]$DryRow.Tag -ne $InitialTag -or
        [string]$DryRow.DevComment -ne $InitialComment)
    {
        throw "Dry Run changed the DataTable package or persisted row values."
    }

    & $RunPatch `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Patch $PatchPath `
        -Policy $PolicyPath `
        -RevisionExport $InitialExport `
        -Mode Commit `
        -Report $CommitReport `
        -ValidationReport $CommitValidation `
        -BackupDir $BackupRoot `
        -Manifest $Manifest
    if ($LASTEXITCODE -ne 0)
    {
        throw "DataTable row-fields Commit failed with exit code $LASTEXITCODE"
    }
    $Commit = [System.IO.File]::ReadAllText($CommitReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ([string]$Commit.operation -ne "setDataTableRowFields" -or
        [int]$Commit.fieldCount -ne 2 -or
        !$Commit.saved -or
        $Commit.rolledBack -or
        [string]$Commit.beforeRevision -ne $InitialRevision -or
        [string]$Commit.afterRevision -eq $InitialRevision -or
        !(Test-Path -LiteralPath $Manifest))
    {
        throw "DataTable row-fields Commit report or manifest is invalid."
    }

    & $RunAssetCatalog -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Asset $AssetPackage -Output $CommitExport
    if ($LASTEXITCODE -ne 0)
    {
        throw "Commit reload export failed with exit code $LASTEXITCODE"
    }
    $CommitCanonical = Read-CanonicalAsset -ExportRoot $CommitExport
    $CommitRow = Read-FixtureRow -Canonical $CommitCanonical
    if ([string]$CommitCanonical.revision.value -ne [string]$Commit.afterRevision -or
        [string]$CommitRow.Tag -ne $NewTag -or
        [string]$CommitRow.DevComment -ne $NewComment)
    {
        throw "Independent Commit reload did not observe both updated DataTable fields."
    }

    & $RunRollback `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Manifest $Manifest `
        -Policy $PolicyPath `
        -BackupRoot $BackupRoot `
        -Mode DryRun `
        -Report $RollbackDryReport
    if ($LASTEXITCODE -ne 0)
    {
        throw "DataTable row-fields rollback Dry Run failed with exit code $LASTEXITCODE"
    }
    $RollbackDry = [System.IO.File]::ReadAllText($RollbackDryReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if (!$RollbackDry.valid -or $RollbackDry.willWriteDisk -or $RollbackDry.restored -or
        [string]$RollbackDry.currentRevision -ne [string]$Commit.afterRevision -or
        [string]$RollbackDry.backupRevision -ne $InitialRevision)
    {
        throw "DataTable row-fields rollback Dry Run report is invalid."
    }

    & $RunRollback `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Manifest $Manifest `
        -Policy $PolicyPath `
        -BackupRoot $BackupRoot `
        -Mode Commit `
        -Report $RollbackCommitReport `
        -VerificationOutput $RollbackExport `
        -VerificationReport $RollbackVerification
    if ($LASTEXITCODE -ne 0)
    {
        throw "DataTable row-fields rollback Commit failed with exit code $LASTEXITCODE"
    }
    $RollbackCommit = [System.IO.File]::ReadAllText($RollbackCommitReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $RollbackVerify = [System.IO.File]::ReadAllText($RollbackVerification, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $RollbackCanonical = Read-CanonicalAsset -ExportRoot $RollbackExport
    $RollbackRow = Read-FixtureRow -Canonical $RollbackCanonical
    if (!$RollbackCommit.restored -or !$RollbackVerify.verified -or
        [string]$RollbackCanonical.revision.value -ne $InitialRevision -or
        [string]$RollbackRow.Tag -ne $InitialTag -or
        [string]$RollbackRow.DevComment -ne $InitialComment)
    {
        throw "DataTable row-fields rollback did not restore the initial Revision and row values."
    }
    $Restored = $true
}
finally
{
    if (!$Restored)
    {
        $Running = @(Get-Process -Name "UnrealEditor", "UnrealEditor-Cmd" -ErrorAction SilentlyContinue)
        if ($Running.Count -eq 0 -and (Test-Path -LiteralPath $EmergencyBackup))
        {
            Copy-Item -LiteralPath $EmergencyBackup -Destination $PackageFile -Force
            Write-Warning "Regression failed; restored the raw emergency package backup."
        }
        else
        {
            Write-Warning "Regression failed and automatic emergency restoration was blocked by a running Unreal process."
        }
    }
}

Write-Host "DataTable atomic row-fields regression passed."
Write-Host "Initial Revision : $InitialRevision"
Write-Host "Commit Revision  : $($Commit.afterRevision)"
Write-Host "Restored Revision: $($RollbackCanonical.revision.value)"
