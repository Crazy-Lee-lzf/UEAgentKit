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
$RunFixture = Join-Path $PSScriptRoot "RunWriteFixturePlan.ps1"
$RunCatalog = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$RunExport = Join-Path $PSScriptRoot "RunExport.ps1"
$RunPatch = Join-Path $PSScriptRoot "RunPatch.ps1"
$RunRollback = Join-Path $PSScriptRoot "RunRollback.ps1"
foreach ($Required in @($RunFixture, $RunCatalog, $RunExport, $RunPatch, $RunRollback))
{
    Assert-UeakPath -Path $Required -Description ([IO.Path]::GetFileName($Required)) -PathType File
}

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output\MultiOperationTransactions"
}
else
{
    $Output = [IO.Path]::GetFullPath($Output)
}
$SafeRoot = [IO.Path]::GetFullPath((Join-Path $ToolRoot "Output"))
$SafePrefix = $SafeRoot.TrimEnd([char]'\', [char]'/') + [IO.Path]::DirectorySeparatorChar
if (!$Output.StartsWith($SafePrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $Output.Equals($SafeRoot, [StringComparison]::OrdinalIgnoreCase))
{
    throw "Output must be a child directory below the tool Output directory: $Output"
}
if (Test-Path -LiteralPath $Output)
{
    Remove-Item -LiteralPath $Output -Recurse -Force
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$ProjectName = [IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$ProjectDir = Split-Path -Parent $ProjectPath
$FixturePlan = Join-Path $ToolRoot "tests\fixtures\multi_operation_transaction_plan.json"
$AssetPackage = "/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset"
$AssetPath = "$AssetPackage.DA_TransactionAsset"
$AssetClass = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
$AssetPackageFile = Join-Path $ProjectDir "Content\UEAgentKitWriteTests\Transactions\DA_TransactionAsset.uasset"
$BlueprintPackage = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint"
$BlueprintPath = "$BlueprintPackage.BP_TransactionBlueprint"
$BlueprintClass = "/Script/Engine.Blueprint"
$BlueprintPackageFile = Join-Path $ProjectDir "Content\UEAgentKitWriteTests\Transactions\BP_TransactionBlueprint.uasset"

function Write-Json([string]$Path, [object]$Value)
{
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    [IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 80) + "`r`n"),
        [Text.UTF8Encoding]::new($false))
}

function Read-Json([string]$Path)
{
    return [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) | ConvertFrom-Json
}

function Get-CanonicalFile([string]$Root, [string]$FileName)
{
    $File = Get-ChildItem -LiteralPath (Join-Path $Root "canonical") -Filter "*.json" -File -Recurse |
        Where-Object { $_.Name -eq $FileName } |
        Select-Object -First 1
    if ($null -eq $File) { throw "Canonical export is missing $FileName below $Root" }
    return $File.FullName
}

function Export-Asset([string]$Name)
{
    $Directory = Join-Path $Output $Name
    $Captured = & $RunCatalog -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Asset $AssetPackage -Output $Directory
    $Captured | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "Data Asset export $Name failed: $LASTEXITCODE" }
    $Canonical = Read-Json (Get-CanonicalFile $Directory "DA_TransactionAsset_DA_TransactionAsset.json")
    if ([string]$Canonical.assetPath -ne $AssetPath) { throw "Unexpected Data Asset export: $($Canonical.assetPath)" }
    return [pscustomobject]@{ Root = $Directory; Value = $Canonical }
}

function Export-Blueprint([string]$Name)
{
    $Directory = Join-Path $Output $Name
    $Captured = & $RunExport `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Asset $BlueprintPackage `
        -Output $Directory `
        -Profile defaults `
        -Format json `
        -IncludeUnchangedDefaults
    $Captured | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "Blueprint export $Name failed: $LASTEXITCODE" }
    $Canonical = Read-Json (Get-CanonicalFile $Directory "BP_TransactionBlueprint_BP_TransactionBlueprint.json")
    if ([string]$Canonical.assetPath -ne $BlueprintPath) { throw "Unexpected Blueprint export: $($Canonical.assetPath)" }
    return [pscustomobject]@{ Root = $Directory; Value = $Canonical }
}

function Get-AssetProperty([object]$Canonical, [string]$Name)
{
    $Property = @($Canonical.assetDetails.properties) |
        Where-Object { [string]$_.name -eq $Name } |
        Select-Object -First 1
    if ($null -eq $Property) { throw "Data Asset export is missing property $Name" }
    return $Property.value
}

function Get-BlueprintVariable([object]$Canonical, [string]$Name)
{
    $Variable = @($Canonical.variables) |
        Where-Object { [string]$_.name -eq $Name } |
        Select-Object -First 1
    if ($null -eq $Variable) { throw "Blueprint export is missing variable $Name" }
    return [string]$Variable.defaultValue
}

function New-AssetPolicy()
{
    return [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests/Transactions")
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @("setAssetProperty")
        allowedAssetClasses = @($AssetClass)
        allowedAssetProperties = @("$AssetClass#BoolValue", "$AssetClass#IntValue")
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 2
        maxValueBytes = 4096
    }
}

function New-BlueprintPolicy()
{
    return [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests/Transactions")
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @("setVariableDefault")
        allowedAssetClasses = @($BlueprintClass)
        allowedAssetProperties = @()
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 2
        maxValueBytes = 4096
    }
}

function Invoke-Transaction(
    [string]$Name,
    [string]$AssetObjectPath,
    [string]$ExpectedClass,
    [string]$ExpectedRevision,
    [string]$RevisionExport,
    [object[]]$Operations,
    [object]$Policy,
    [ValidateSet("DryRun", "Commit")][string]$Mode,
    [string]$Manifest = "")
{
    $PatchPath = Join-Path $Output "$Name.patch.json"
    $PolicyPath = Join-Path $Output "$Name.policy.json"
    $ReportPath = Join-Path $Output "$Name.$($Mode.ToLower()).report.json"
    $ValidationPath = Join-Path $Output "$Name.$($Mode.ToLower()).validation.json"
    $BackupRoot = Join-Path $Output "Backups\$Name"
    Write-Json $PolicyPath $Policy
    Write-Json $PatchPath ([ordered]@{
        schemaVersion = "1.0"
        patchId = $Name
        projectName = $ProjectName
        description = "Single-asset multi-operation transaction regression"
        assets = @([ordered]@{
            assetPath = $AssetObjectPath
            expectedRevision = $ExpectedRevision
            expectedAssetClass = $ExpectedClass
            operations = $Operations
        })
    })
    $Arguments = @{
        EngineRoot = $EngineRoot
        ProjectPath = $ProjectPath
        Patch = $PatchPath
        Policy = $PolicyPath
        RevisionExport = $RevisionExport
        Mode = $Mode
        Report = $ReportPath
        ValidationReport = $ValidationPath
        BackupDir = $BackupRoot
    }
    if (![string]::IsNullOrWhiteSpace($Manifest)) { $Arguments.Manifest = $Manifest }
    $Captured = & $RunPatch @Arguments
    $Captured | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "$Name $Mode failed: $LASTEXITCODE" }
    return [pscustomobject]@{
        Report = Read-Json $ReportPath
        Policy = $PolicyPath
        BackupRoot = $BackupRoot
        Manifest = $Manifest
        Patch = $PatchPath
    }
}

function Assert-DryRun([object]$Transaction, [string]$Name)
{
    $Report = $Transaction.Report
    if ([string]$Report.operation -ne "transaction" -or
        [string]$Report.transactionKind -ne "single-asset-multi-operation" -or
        [int]$Report.operationCount -ne 2 -or !$Report.atomic -or $Report.saved -or
        !$Report.rolledBack -or !$Report.rollbackValueMatch -or !$Report.diskUnchanged -or
        [string]$Report.rollbackStrategy -ne "process-discard" -or
        [string]$Report.beforeRevision -ne [string]$Report.afterRevision -or
        ![string]::IsNullOrWhiteSpace([string]$Report.backupPath) -or
        @($Report.operations).Count -ne 2)
    {
        throw "$Name Dry Run atomicity report gates failed"
    }
    if (@(Get-ChildItem -LiteralPath $Transaction.BackupRoot -Filter "*.bak" -File -Recurse -ErrorAction SilentlyContinue).Count -ne 0)
    {
        throw "$Name Dry Run created a package backup"
    }
}

function Assert-Commit([object]$Transaction, [string]$Name, [string]$BeforeRevision)
{
    $Report = $Transaction.Report
    if ([string]$Report.operation -ne "transaction" -or
        [int]$Report.operationCount -ne 2 -or !$Report.atomic -or !$Report.saved -or
        $Report.rolledBack -or [string]$Report.rollbackStrategy -ne "package-backup" -or
        [string]$Report.beforeRevision -ne $BeforeRevision -or
        [string]$Report.afterRevision -eq $BeforeRevision -or
        @($Report.operations).Count -ne 2)
    {
        throw "$Name Commit transaction report gates failed"
    }
    $Backups = @(Get-ChildItem -LiteralPath $Transaction.BackupRoot -Filter "*.bak" -File -Recurse)
    $ReportedBackup = if ([string]::IsNullOrWhiteSpace([string]$Report.backupPath))
    {
        ""
    }
    else
    {
        [IO.Path]::GetFullPath([string]$Report.backupPath)
    }
    if ($Backups.Count -ne 1 -or
        !$Backups[0].FullName.Equals($ReportedBackup, [StringComparison]::OrdinalIgnoreCase))
    {
        throw "$Name Commit did not create exactly one reported package backup"
    }
    $Manifest = Read-Json $Transaction.Manifest
    if ([int]$Manifest.operationCount -ne 2 -or [string]$Manifest.operation -ne "transaction" -or
        @($Manifest.operations).Count -ne 2)
    {
        throw "$Name backup manifest transaction metadata is invalid"
    }
}

function Invoke-TransactionRollback([object]$Transaction, [string]$Name)
{
    $ReportPath = Join-Path $Output "$Name.rollback.json"
    $VerifyRoot = Join-Path $Output "$Name.rollback-verify"
    $VerifyReport = Join-Path $Output "$Name.rollback-verify.json"
    $Captured = & $RunRollback `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Manifest $Transaction.Manifest `
        -Policy $Transaction.Policy `
        -BackupRoot $Transaction.BackupRoot `
        -Mode Commit `
        -Report $ReportPath `
        -VerificationOutput $VerifyRoot `
        -VerificationReport $VerifyReport
    $Captured | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "$Name rollback failed: $LASTEXITCODE" }
    $Rollback = Read-Json $ReportPath
    $Verification = Read-Json $VerifyReport
    if (!$Rollback.restored -or !$Verification.verified) { throw "$Name rollback verification failed" }
}

& $RunFixture `
    -EngineRoot $EngineRoot `
    -ProjectPath $ProjectPath `
    -Plan $FixturePlan `
    -Mode Reset `
    -Report (Join-Path $Output "fixture-report.json") `
    -ValidationReport (Join-Path $Output "fixture-validation.json") `
    -VerificationOutput (Join-Path $Output "fixture-reload") `
    -VerificationReport (Join-Path $Output "fixture-verification.json")
if ($LASTEXITCODE -ne 0) { throw "Transaction Fixture reset failed: $LASTEXITCODE" }
Assert-UeakPath -Path $AssetPackageFile -Description "Transaction Data Asset fixture" -PathType File
Assert-UeakPath -Path $BlueprintPackageFile -Description "Transaction Blueprint fixture" -PathType File
$AssetEmergency = Join-Path $Output "asset-baseline.uasset"
$BlueprintEmergency = Join-Path $Output "blueprint-baseline.uasset"
Copy-Item -LiteralPath $AssetPackageFile -Destination $AssetEmergency -Force
Copy-Item -LiteralPath $BlueprintPackageFile -Destination $BlueprintEmergency -Force
$Restored = $false

try
{
    $InitialAsset = Export-Asset "AssetInitial"
    $InitialAssetRevision = [string]$InitialAsset.Value.revision.value
    if ((Get-AssetProperty $InitialAsset.Value "BoolValue") -ne $false -or
        [int](Get-AssetProperty $InitialAsset.Value "IntValue") -ne -17)
    {
        throw "Unexpected Data Asset transaction fixture baseline"
    }
    $AssetOperations = @(
        [ordered]@{
            operationId = "asset-bool"
            operation = "setAssetProperty"
            target = [ordered]@{ propertyPath = "BoolValue" }
            value = $true
        },
        [ordered]@{
            operationId = "asset-int"
            operation = "setAssetProperty"
            target = [ordered]@{ propertyPath = "IntValue" }
            value = 2048
        }
    )
    $AssetDry = Invoke-Transaction "asset-transaction-dry" $AssetPath $AssetClass $InitialAssetRevision $InitialAsset.Root $AssetOperations (New-AssetPolicy) "DryRun"
    Assert-DryRun $AssetDry "Data Asset"
    $AssetAfterDry = Export-Asset "AssetAfterDry"
    if ([string]$AssetAfterDry.Value.revision.value -ne $InitialAssetRevision -or
        (Get-AssetProperty $AssetAfterDry.Value "BoolValue") -ne $false -or
        [int](Get-AssetProperty $AssetAfterDry.Value "IntValue") -ne -17)
    {
        throw "Data Asset Dry Run changed independent disk state"
    }
    $AssetManifest = Join-Path $Output "Backups\asset-transaction\asset.manifest.json"
    $AssetCommit = Invoke-Transaction "asset-transaction" $AssetPath $AssetClass $InitialAssetRevision $AssetAfterDry.Root $AssetOperations (New-AssetPolicy) "Commit" $AssetManifest
    Assert-Commit $AssetCommit "Data Asset" $InitialAssetRevision
    foreach ($Operation in @($AssetCommit.Report.operations))
    {
        if (@($Operation.authorizationKeys).Count -ne 1) { throw "Data Asset manifest authorization evidence is incomplete" }
    }
    $AssetCommitted = Export-Asset "AssetCommitted"
    if ([string]$AssetCommitted.Value.revision.value -ne [string]$AssetCommit.Report.afterRevision -or
        (Get-AssetProperty $AssetCommitted.Value "BoolValue") -ne $true -or
        [int](Get-AssetProperty $AssetCommitted.Value "IntValue") -ne 2048)
    {
        throw "Data Asset Commit independent verification failed"
    }
    Invoke-TransactionRollback $AssetCommit "asset-transaction"
    $AssetFinal = Export-Asset "AssetFinal"
    if ([string]$AssetFinal.Value.revision.value -ne $InitialAssetRevision -or
        (Get-AssetProperty $AssetFinal.Value "BoolValue") -ne $false -or
        [int](Get-AssetProperty $AssetFinal.Value "IntValue") -ne -17)
    {
        throw "Data Asset transaction rollback did not restore baseline"
    }

    $InitialBlueprint = Export-Blueprint "BlueprintInitial"
    $InitialBlueprintRevision = [string]$InitialBlueprint.Value.revision.value
    if ((Get-BlueprintVariable $InitialBlueprint.Value "TransactionInt") -ne "0" -or
        (Get-BlueprintVariable $InitialBlueprint.Value "TransactionFlag") -ne "False")
    {
        throw "Unexpected Blueprint transaction fixture baseline"
    }
    $BlueprintOperations = @(
        [ordered]@{
            operationId = "blueprint-int"
            operation = "setVariableDefault"
            target = [ordered]@{ variableName = "TransactionInt" }
            value = 42
        },
        [ordered]@{
            operationId = "blueprint-flag"
            operation = "setVariableDefault"
            target = [ordered]@{ variableName = "TransactionFlag" }
            value = $true
        }
    )
    $BlueprintDry = Invoke-Transaction "blueprint-transaction-dry" $BlueprintPath $BlueprintClass $InitialBlueprintRevision $InitialBlueprint.Root $BlueprintOperations (New-BlueprintPolicy) "DryRun"
    Assert-DryRun $BlueprintDry "Blueprint"
    $BlueprintAfterDry = Export-Blueprint "BlueprintAfterDry"
    if ([string]$BlueprintAfterDry.Value.revision.value -ne $InitialBlueprintRevision -or
        (Get-BlueprintVariable $BlueprintAfterDry.Value "TransactionInt") -ne "0" -or
        (Get-BlueprintVariable $BlueprintAfterDry.Value "TransactionFlag") -ne "False")
    {
        throw "Blueprint Dry Run changed independent disk state"
    }
    $BlueprintManifest = Join-Path $Output "Backups\blueprint-transaction\blueprint.manifest.json"
    $BlueprintCommit = Invoke-Transaction "blueprint-transaction" $BlueprintPath $BlueprintClass $InitialBlueprintRevision $BlueprintAfterDry.Root $BlueprintOperations (New-BlueprintPolicy) "Commit" $BlueprintManifest
    Assert-Commit $BlueprintCommit "Blueprint" $InitialBlueprintRevision
    $BlueprintCommitted = Export-Blueprint "BlueprintCommitted"
    if ([string]$BlueprintCommitted.Value.revision.value -ne [string]$BlueprintCommit.Report.afterRevision -or
        (Get-BlueprintVariable $BlueprintCommitted.Value "TransactionInt") -ne "42" -or
        (Get-BlueprintVariable $BlueprintCommitted.Value "TransactionFlag") -ne "True")
    {
        throw "Blueprint Commit independent verification failed"
    }
    Invoke-TransactionRollback $BlueprintCommit "blueprint-transaction"
    $BlueprintFinal = Export-Blueprint "BlueprintFinal"
    if ([string]$BlueprintFinal.Value.revision.value -ne $InitialBlueprintRevision -or
        (Get-BlueprintVariable $BlueprintFinal.Value "TransactionInt") -ne "0" -or
        (Get-BlueprintVariable $BlueprintFinal.Value "TransactionFlag") -ne "False")
    {
        throw "Blueprint transaction rollback did not restore baseline"
    }

    Write-Json (Join-Path $Output "summary.json") ([ordered]@{
        passed = $true
        asset = [ordered]@{
            assetPath = $AssetPath
            operationCount = 2
            baselineRevision = $InitialAssetRevision
            committedRevision = [string]$AssetCommit.Report.afterRevision
            finalRevision = [string]$AssetFinal.Value.revision.value
        }
        blueprint = [ordered]@{
            assetPath = $BlueprintPath
            operationCount = 2
            baselineRevision = $InitialBlueprintRevision
            committedRevision = [string]$BlueprintCommit.Report.afterRevision
            finalRevision = [string]$BlueprintFinal.Value.revision.value
        }
    })
    $Restored = $true
}
finally
{
    if (!$Restored -and @(Get-Process UnrealEditor,UnrealEditor-Cmd -ErrorAction SilentlyContinue).Count -eq 0)
    {
        Copy-Item -LiteralPath $AssetEmergency -Destination $AssetPackageFile -Force
        Copy-Item -LiteralPath $BlueprintEmergency -Destination $BlueprintPackageFile -Force
        Write-Warning "Transaction regression failed; raw baseline packages restored"
    }
}

Write-Host "Single-asset multi-operation transaction regression passed."
Write-Host "Asset=$AssetPath"
Write-Host "Blueprint=$BlueprintPath"
Write-Host "OperationsPerAsset=2"
