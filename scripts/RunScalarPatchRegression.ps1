param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Output = "",
    [string]$FixturePlan = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$ProjectName = [System.IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$PatchScript = Join-Path $PSScriptRoot "RunPatch.ps1"
$CatalogScript = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$FixtureScript = Join-Path $PSScriptRoot "RunWriteFixturePlan.ps1"

foreach ($RequiredScript in @($PatchScript, $CatalogScript, $FixtureScript))
{
    Assert-UeakPath -Path $RequiredScript -Description ([System.IO.Path]::GetFileName($RequiredScript)) -PathType File
}

if ([string]::IsNullOrWhiteSpace($FixturePlan))
{
    $FixturePlan = Join-Path $ToolRoot "tests\fixtures\scalar_patch_regression_plan.json"
}
else
{
    $FixturePlan = [System.IO.Path]::GetFullPath($FixturePlan)
}
Assert-UeakPath -Path $FixturePlan -Description "Scalar regression fixture plan" -PathType File

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output\ScalarPatchRegression"
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
$OutputCursor = [System.IO.DirectoryInfo]$Output
while ($null -ne $OutputCursor)
{
    if ($OutputCursor.Exists -and
        (($OutputCursor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0))
    {
        throw "Output path must not traverse a Junction or symbolic link: $($OutputCursor.FullName)"
    }
    if ($OutputCursor.FullName.Equals($SafeOutputRoot, [System.StringComparison]::OrdinalIgnoreCase))
    {
        break
    }
    $OutputCursor = $OutputCursor.Parent
}
$OutputPrefix = $Output.TrimEnd([char]'\', [char]'/') + [System.IO.Path]::DirectorySeparatorChar
if ($FixturePlan.Equals($Output, [System.StringComparison]::OrdinalIgnoreCase) -or
    $FixturePlan.StartsWith($OutputPrefix, [System.StringComparison]::OrdinalIgnoreCase))
{
    throw "FixturePlan must stay outside the regression Output directory: $FixturePlan"
}

if (Test-Path -LiteralPath $Output)
{
    $NestedReparsePoint = Get-ChildItem -LiteralPath $Output -Force -Recurse -ErrorAction Stop |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 } |
        Select-Object -First 1
    if ($null -ne $NestedReparsePoint)
    {
        throw "Output contains a Junction or symbolic link and cannot be recursively cleared: $($NestedReparsePoint.FullName)"
    }
    Remove-Item -LiteralPath $Output -Recurse -Force
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$AssetPackage = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget"
$AssetPath = "$AssetPackage.DA_ScalarPatchTarget"
$AssetClass = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
$BackupDir = Join-Path $Output "Backups"
$PolicyPath = Join-Path $Output "policy.json"
$SummaryPath = Join-Path $Output "summary.json"

$Cases = @(
    [pscustomobject]@{ Name = "BoolValue"; Value = $true; Baseline = $false; TargetType = "BoolProperty" },
    [pscustomobject]@{ Name = "ByteValue"; Value = 201; Baseline = 7; TargetType = "ByteProperty" },
    [pscustomobject]@{ Name = "IntValue"; Value = 2048; Baseline = -17; TargetType = "IntProperty" },
    [pscustomobject]@{ Name = "Int64Value"; Value = -4000000000000; Baseline = 1234567890123; TargetType = "Int64Property" },
    [pscustomobject]@{ Name = "FloatValue"; Value = 3.75; Baseline = 1.25; TargetType = "FloatProperty" },
    [pscustomobject]@{ Name = "DoubleValue"; Value = 123.125; Baseline = -2.5; TargetType = "DoubleProperty" },
    [pscustomobject]@{ Name = "StringValue"; Value = "Updated String 0.5.0"; Baseline = "Initial String"; TargetType = "StrProperty" },
    [pscustomobject]@{ Name = "NameValue"; Value = "UpdatedName044"; Baseline = "InitialName"; TargetType = "NameProperty" },
    [pscustomobject]@{ Name = "TextValue"; Value = "Updated Text 0.5.0"; Baseline = "Initial Text"; TargetType = "TextProperty" },
    [pscustomobject]@{ Name = "EnumValue"; Value = "Beta"; Baseline = "Alpha"; TargetType = "EnumProperty" },
    [pscustomobject]@{ Name = "LegacyEnumValue"; Value = "UEAK_LegacyBeta"; Baseline = "UEAK_LegacyAlpha"; TargetType = "ByteProperty" }
)

function Write-Utf8Json
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Value,
        [int]$Depth = 20
    )
    $Directory = [System.IO.Path]::GetDirectoryName($Path)
    if (![string]::IsNullOrWhiteSpace($Directory))
    {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    }
    $Json = $Value | ConvertTo-Json -Depth $Depth
    [System.IO.File]::WriteAllText(
        $Path,
        $Json + "`r`n",
        [System.Text.UTF8Encoding]::new($false))
}

function Read-Utf8Json
{
    param([Parameter(Mandatory = $true)][string]$Path)
    $Json = [System.IO.File]::ReadAllText(
        $Path,
        [System.Text.UTF8Encoding]::new($false))
    return $Json | ConvertFrom-Json
}

function Read-Canonical
{
    param([Parameter(Mandatory = $true)][string]$ExportRoot)
    $Matches = @(Get-ChildItem -LiteralPath (Join-Path $ExportRoot "canonical") -Filter *.json -File -Recurse |
        Where-Object { (Read-Utf8Json -Path $_.FullName).assetPath -eq $AssetPath })
    if ($Matches.Count -ne 1)
    {
        throw "Expected one canonical scalar fixture in $ExportRoot, found $($Matches.Count)."
    }
    return (Read-Utf8Json -Path $Matches[0].FullName)
}

function Get-PropertyMap
{
    param([Parameter(Mandatory = $true)][object]$Canonical)
    $Map = @{}
    foreach ($Property in @($Canonical.assetDetails.properties))
    {
        $Map[[string]$Property.name] = $Property.value
    }
    return $Map
}

function Assert-Values
{
    param(
        [Parameter(Mandatory = $true)][object]$Canonical,
        [Parameter(Mandatory = $true)][hashtable]$Expected,
        [Parameter(Mandatory = $true)][string]$Context
    )
    if ($Canonical.assetClass -ne $AssetClass)
    {
        throw "$Context assetClass mismatch: $($Canonical.assetClass)"
    }
    if ($Canonical.revision.packageDirty -ne $false)
    {
        throw "$Context package is dirty."
    }
    $Map = Get-PropertyMap -Canonical $Canonical
    foreach ($Name in $Expected.Keys)
    {
        if (!$Map.ContainsKey($Name))
        {
            throw "$Context missing property: $Name"
        }
        $ActualJson = $Map[$Name] | ConvertTo-Json -Compress
        $ExpectedJson = $Expected[$Name] | ConvertTo-Json -Compress
        if ($ActualJson -ne $ExpectedJson)
        {
            throw "$Context property $Name mismatch. Expected=$ExpectedJson Actual=$ActualJson"
        }
    }
}

function Export-ScalarAsset
{
    param([Parameter(Mandatory = $true)][string]$Directory)
    if (Test-Path -LiteralPath $Directory)
    {
        Remove-Item -LiteralPath $Directory -Recurse -Force
    }
    & $CatalogScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Asset $AssetPackage `
        -Output $Directory | Out-Host
    if ($LASTEXITCODE -ne 0)
    {
        throw "Scalar asset export failed with exit code $LASTEXITCODE"
    }
    return Read-Canonical -ExportRoot $Directory
}

function New-ScalarPatch
{
    param(
        [Parameter(Mandatory = $true)][object]$Case,
        [Parameter(Mandatory = $true)][string]$Revision,
        [Parameter(Mandatory = $true)][string]$PatchId,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $Patch = [ordered]@{
        schemaVersion = "1.0"
        patchId = $PatchId
        projectName = $ProjectName
        description = "UEAgentKit scalar regression for $($Case.Name)."
        assets = @(
            [ordered]@{
                assetPath = $AssetPath
                expectedRevision = $Revision
                expectedAssetClass = $AssetClass
                operations = @(
                    [ordered]@{
                        operationId = "set-$($Case.Name)"
                        operation = "setAssetProperty"
                        target = [ordered]@{ propertyPath = $Case.Name }
                        value = $Case.Value
                    }
                )
            }
        )
    }
    Write-Utf8Json -Path $Path -Value $Patch
}

function Invoke-RejectedScalarPatch
{
    param(
        [Parameter(Mandatory = $true)][string]$CaseName,
        [Parameter(Mandatory = $true)][string]$PropertyName,
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][string[]]$AllowedProperties,
        [string]$ExpectedValidationCode = "",
        [ValidateSet("DryRun", "Commit")][string]$Mode = "DryRun",
        [string]$TestFailureInjection = "",
        [int]$ExpectedUnrealExitCode = 0,
        [switch]$ExpectBackup
    )
    $CaseDirectory = Join-Path (Join-Path $Output "Failures") $CaseName
    $PatchPath = Join-Path $CaseDirectory "patch.json"
    $CasePolicyPath = Join-Path $CaseDirectory "policy.json"
    $ReportPath = Join-Path $CaseDirectory "report.json"
    $ValidationPath = Join-Path $CaseDirectory "validation.json"
    $Patch = [ordered]@{
        schemaVersion = "1.0"
        patchId = "scalar-failure-$CaseName"
        projectName = $ProjectName
        description = "Expected scalar patch rejection: $CaseName."
        assets = @(
            [ordered]@{
                assetPath = $AssetPath
                expectedRevision = $ExpectedRevision
                expectedAssetClass = $AssetClass
                operations = @(
                    [ordered]@{
                        operationId = "reject-$CaseName"
                        operation = "setAssetProperty"
                        target = [ordered]@{ propertyPath = $PropertyName }
                        value = $Value
                    }
                )
            }
        )
    }
    $CasePolicy = [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests/ScalarRegression")
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @("setAssetProperty")
        allowedAssetClasses = @($AssetClass)
        allowedAssetProperties = @($AllowedProperties | ForEach-Object { "$AssetClass#$_" })
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    }
    Write-Utf8Json -Path $PatchPath -Value $Patch
    Write-Utf8Json -Path $CasePolicyPath -Value $CasePolicy
    $BeforeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TargetPackagePath).Hash.ToLowerInvariant()
    $BeforeBackupPaths = @()
    if ($ExpectBackup)
    {
        $BeforeBackupPaths = @(Get-ChildItem -LiteralPath $BackupDir -Filter *.bak -File -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName })
    }
    $Rejected = $false
    $FailureMessage = ""
    try
    {
        $RunPatchArguments = @{
            EngineRoot = $EngineRoot
            ProjectPath = $ProjectPath
            Patch = $PatchPath
            Policy = $CasePolicyPath
            RevisionExport = $FailureRevisionExport
            Mode = $Mode
            Report = $ReportPath
            ValidationReport = $ValidationPath
            BackupDir = $BackupDir
        }
        if (![string]::IsNullOrWhiteSpace($TestFailureInjection))
        {
            $RunPatchArguments.TestFailureInjection = $TestFailureInjection
        }
        & $PatchScript @RunPatchArguments | Out-Host
    }
    catch
    {
        $Rejected = $true
        $FailureMessage = $_.Exception.Message
    }
    if (!$Rejected)
    {
        throw "Failure case $CaseName was unexpectedly accepted."
    }
    $AfterHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TargetPackagePath).Hash.ToLowerInvariant()
    if ($AfterHash -ne $BeforeHash)
    {
        throw "Failure case $CaseName changed the scalar fixture package."
    }
    if (!(Test-Path -LiteralPath $ValidationPath))
    {
        throw "Failure case $CaseName did not write a validation report."
    }
    $Validation = Read-Utf8Json -Path $ValidationPath
    $ValidationCodes = @($Validation.errors | ForEach-Object { [string]$_.code })
    $FailureStage = "unreal"
    if (![string]::IsNullOrWhiteSpace($ExpectedValidationCode))
    {
        $FailureStage = "validation"
        if ($Validation.valid -or !($ValidationCodes -contains $ExpectedValidationCode))
        {
            throw "Failure case $CaseName did not return validation code $ExpectedValidationCode."
        }
    }
    elseif (!$Validation.valid -or
        $ExpectedUnrealExitCode -le 0 -or
        $FailureMessage -notlike "*AssetPatch failed with exit code $ExpectedUnrealExitCode*")
    {
        throw "Failure case $CaseName was expected to reach AssetPatch exit code $ExpectedUnrealExitCode."
    }
    $FailureBackupPath = ""
    if ($ExpectBackup)
    {
        $NewBackups = @(Get-ChildItem -LiteralPath $BackupDir -Filter *.bak -File |
            Where-Object { $BeforeBackupPaths -notcontains $_.FullName })
        if ($NewBackups.Count -ne 1)
        {
            throw "Failure case $CaseName expected one new raw backup, found $($NewBackups.Count)."
        }
        $FailureBackupPath = $NewBackups[0].FullName
        $BackupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $FailureBackupPath).Hash.ToLowerInvariant()
        if ($BackupHash -ne $BeforeHash)
        {
            throw "Failure case $CaseName backup does not match the pre-Commit package."
        }
        if (Test-Path -LiteralPath ($FailureBackupPath + ".manifest.json"))
        {
            throw "Failure case $CaseName must not create a success Manifest."
        }
    }
    return [pscustomobject]@{
        name = $CaseName
        property = $PropertyName
        stage = $FailureStage
        mode = $Mode
        testFailureInjection = $TestFailureInjection
        expectedValidationCode = $ExpectedValidationCode
        expectedUnrealExitCode = $ExpectedUnrealExitCode
        validationCodes = $ValidationCodes
        failureMessage = $FailureMessage
        diskUnchanged = $true
        beforeHash = $BeforeHash
        afterHash = $AfterHash
        validationPath = $ValidationPath
        reportExists = [bool](Test-Path -LiteralPath $ReportPath)
        backupPath = $FailureBackupPath
    }
}

$Policy = [ordered]@{
    schemaVersion = "1.0"
    validationEnabled = $true
    commitEnabled = $true
    allowedProjectNames = @($ProjectName)
    allowedAssetRoots = @("/Game/UEAgentKitWriteTests/ScalarRegression")
    allowedReferenceRoots = @()
    allowedReferenceClasses = @()
    allowedOperations = @("setAssetProperty")
    allowedAssetClasses = @($AssetClass)
    allowedAssetProperties = @($Cases | ForEach-Object { "$AssetClass#$($_.Name)" })
    allowedMaterialParameters = @()
    allowedDataTableFields = @()
    requireRevision = $true
    rejectDirtyPackages = $true
    maxAssetsPerPatch = 1
    maxOperationsPerAsset = 1
    maxValueBytes = 4096
}
Write-Utf8Json -Path $PolicyPath -Value $Policy

$BaselineValues = @{}
$CommittedValues = @{}
foreach ($Case in $Cases)
{
    $BaselineValues[$Case.Name] = $Case.Baseline
    $CommittedValues[$Case.Name] = $Case.Baseline
}

$FixtureDirectory = Join-Path $Output "Fixture"
Write-Host "Resetting scalar fixture..."
& $FixtureScript `
    -EngineRoot $EngineRoot `
    -ProjectPath $ProjectPath `
    -Plan $FixturePlan `
    -Mode Reset `
    -Report (Join-Path $FixtureDirectory "fixture-report.json") `
    -ValidationReport (Join-Path $FixtureDirectory "validation-report.json") `
    -VerificationOutput (Join-Path $FixtureDirectory "Reload") `
    -VerificationReport (Join-Path $FixtureDirectory "verification-report.json")
if ($LASTEXITCODE -ne 0)
{
    throw "Scalar fixture reset failed with exit code $LASTEXITCODE"
}

$CurrentExport = Join-Path $Output "Revision-00"
$CurrentCanonical = Export-ScalarAsset -Directory $CurrentExport
Assert-Values -Canonical $CurrentCanonical -Expected $BaselineValues -Context "Initial fixture"
$InitialRevision = [string]$CurrentCanonical.revision.value
$TargetPackagePath = [string](
    (Read-Utf8Json -Path (Join-Path $FixtureDirectory "fixture-report.json")).fixtures[0].packageFilename)
$InitialDiskHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TargetPackagePath).Hash.ToLowerInvariant()

$DryRunResults = @()
$DryRunDirectory = Join-Path $Output "DryRun"
for ($Index = 0; $Index -lt $Cases.Count; ++$Index)
{
    $Case = $Cases[$Index]
    $CaseDirectory = Join-Path $DryRunDirectory ("{0:D2}-{1}" -f ($Index + 1), $Case.Name)
    $PatchPath = Join-Path $CaseDirectory "patch.json"
    $ReportPath = Join-Path $CaseDirectory "report.json"
    $ValidationPath = Join-Path $CaseDirectory "validation.json"
    New-ScalarPatch `
        -Case $Case `
        -Revision $InitialRevision `
        -PatchId ("scalar-dryrun-{0:D2}-{1}" -f ($Index + 1), $Case.Name) `
        -Path $PatchPath
    Write-Host "Dry Run $($Index + 1)/$($Cases.Count): $($Case.Name)"
    & $PatchScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Patch $PatchPath `
        -Policy $PolicyPath `
        -RevisionExport $CurrentExport `
        -Mode DryRun `
        -Report $ReportPath `
        -ValidationReport $ValidationPath `
        -BackupDir $BackupDir
    if ($LASTEXITCODE -ne 0)
    {
        throw "Dry Run failed for $($Case.Name) with exit code $LASTEXITCODE"
    }
    $Report = Read-Utf8Json -Path $ReportPath
    if ($Report.mode -ne "DryRun" -or
        $Report.targetType -ne $Case.TargetType -or
        !$Report.rolledBack -or
        !$Report.rollbackValueMatch -or
        !$Report.diskUnchanged -or
        $Report.beforeRevision -ne $InitialRevision -or
        $Report.afterRevision -ne $InitialRevision -or
        $Report.beforeValue -ne $Report.restoredValue)
    {
        throw "Dry Run report verification failed for $($Case.Name)."
    }
    $DiskHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TargetPackagePath).Hash.ToLowerInvariant()
    if ($DiskHash -ne $InitialDiskHash)
    {
        throw "Dry Run changed the scalar fixture package for $($Case.Name)."
    }
    $DryRunResults += [pscustomobject]@{
        property = $Case.Name
        targetType = $Report.targetType
        beforeValue = $Report.beforeValue
        afterValue = $Report.afterValue
        restoredValue = $Report.restoredValue
        rollbackValueMatch = [bool]$Report.rollbackValueMatch
        diskUnchanged = [bool]$Report.diskUnchanged
        reportPath = $ReportPath
    }
}

$AfterDryRuns = Export-ScalarAsset -Directory (Join-Path $Output "AfterDryRuns")
Assert-Values -Canonical $AfterDryRuns -Expected $BaselineValues -Context "After all Dry Runs"
if ($AfterDryRuns.revision.value -ne $InitialRevision)
{
    throw "Scalar fixture Revision changed after Dry Runs."
}

$CommitResults = @()
$CommitDirectory = Join-Path $Output "Commit"
$CurrentRevision = $InitialRevision
for ($Index = 0; $Index -lt $Cases.Count; ++$Index)
{
    $Case = $Cases[$Index]
    $CaseDirectory = Join-Path $CommitDirectory ("{0:D2}-{1}" -f ($Index + 1), $Case.Name)
    $PatchPath = Join-Path $CaseDirectory "patch.json"
    $ReportPath = Join-Path $CaseDirectory "report.json"
    $ValidationPath = Join-Path $CaseDirectory "validation.json"
    New-ScalarPatch `
        -Case $Case `
        -Revision $CurrentRevision `
        -PatchId ("scalar-commit-{0:D2}-{1}" -f ($Index + 1), $Case.Name) `
        -Path $PatchPath
    Write-Host "Commit $($Index + 1)/$($Cases.Count): $($Case.Name)"
    & $PatchScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Patch $PatchPath `
        -Policy $PolicyPath `
        -RevisionExport $CurrentExport `
        -Mode Commit `
        -Report $ReportPath `
        -ValidationReport $ValidationPath `
        -BackupDir $BackupDir
    if ($LASTEXITCODE -ne 0)
    {
        throw "Commit failed for $($Case.Name) with exit code $LASTEXITCODE"
    }
    $Report = Read-Utf8Json -Path $ReportPath
    if ($Report.mode -ne "Commit" -or
        $Report.targetType -ne $Case.TargetType -or
        !$Report.saved -or
        $Report.rolledBack -or
        $Report.beforeRevision -ne $CurrentRevision -or
        $Report.afterRevision -eq $CurrentRevision -or
        !(Test-Path -LiteralPath ([string]$Report.backupPath)) -or
        !(Test-Path -LiteralPath (([string]$Report.backupPath) + ".manifest.json")))
    {
        throw "Commit report verification failed for $($Case.Name)."
    }
    $CommittedValues[$Case.Name] = $Case.Value
    $NextExport = Join-Path $Output ("Revision-{0:D2}" -f ($Index + 1))
    $Reloaded = Export-ScalarAsset -Directory $NextExport
    Assert-Values -Canonical $Reloaded -Expected $CommittedValues -Context "Commit reload $($Case.Name)"
    if ($Reloaded.revision.value -ne $Report.afterRevision)
    {
        throw "Reloaded Revision does not match Commit report for $($Case.Name)."
    }
    $CommitResults += [pscustomobject]@{
        property = $Case.Name
        targetType = $Report.targetType
        beforeValue = $Report.beforeValue
        afterValue = $Report.afterValue
        beforeRevision = $Report.beforeRevision
        afterRevision = $Report.afterRevision
        backupPath = $Report.backupPath
        manifestPath = ([string]$Report.backupPath) + ".manifest.json"
        reloadExport = $NextExport
        reportPath = $ReportPath
    }
    $CurrentRevision = [string]$Report.afterRevision
    $CurrentExport = $NextExport
}

$FinalCanonical = Read-Canonical -ExportRoot $CurrentExport
Assert-Values -Canonical $FinalCanonical -Expected $CommittedValues -Context "Final committed scalar matrix"

$ResetDirectory = Join-Path $Output "FinalReset"
Write-Host "Resetting scalar fixture to baseline..."
& $FixtureScript `
    -EngineRoot $EngineRoot `
    -ProjectPath $ProjectPath `
    -Plan $FixturePlan `
    -Mode Reset `
    -Report (Join-Path $ResetDirectory "fixture-report.json") `
    -ValidationReport (Join-Path $ResetDirectory "validation-report.json") `
    -VerificationOutput (Join-Path $ResetDirectory "Reload") `
    -VerificationReport (Join-Path $ResetDirectory "verification-report.json")
if ($LASTEXITCODE -ne 0)
{
    throw "Final scalar fixture reset failed with exit code $LASTEXITCODE"
}
$ResetCanonical = Export-ScalarAsset -Directory (Join-Path $ResetDirectory "Canonical")
Assert-Values -Canonical $ResetCanonical -Expected $BaselineValues -Context "Final reset"
$FailureRevisionExport = Join-Path $ResetDirectory "Canonical"
$ResetRevision = [string]$ResetCanonical.revision.value
$FailureResults = @()
Write-Host "Running expected failure matrix..."
$FailureResults += Invoke-RejectedScalarPatch `
    -CaseName "unauthorized-property" `
    -PropertyName "StringValue" `
    -Value "Rejected" `
    -ExpectedRevision $ResetRevision `
    -AllowedProperties @("BoolValue") `
    -ExpectedValidationCode "asset-property-not-allowed"
$FailureResults += Invoke-RejectedScalarPatch `
    -CaseName "stale-revision" `
    -PropertyName "BoolValue" `
    -Value $true `
    -ExpectedRevision ("sha256:" + ("0" * 64)) `
    -AllowedProperties @("BoolValue") `
    -ExpectedValidationCode "revision-conflict"
$FailureResults += Invoke-RejectedScalarPatch `
    -CaseName "wrong-json-type" `
    -PropertyName "IntValue" `
    -Value "not-a-number" `
    -ExpectedRevision $ResetRevision `
    -AllowedProperties @("IntValue") `
    -ExpectedUnrealExitCode 20
$FailureResults += Invoke-RejectedScalarPatch `
    -CaseName "byte-out-of-range" `
    -PropertyName "ByteValue" `
    -Value 300 `
    -ExpectedRevision $ResetRevision `
    -AllowedProperties @("ByteValue") `
    -ExpectedUnrealExitCode 20
$FailureResults += Invoke-RejectedScalarPatch `
    -CaseName "invalid-enum-name" `
    -PropertyName "EnumValue" `
    -Value "MissingValue" `
    -ExpectedRevision $ResetRevision `
    -AllowedProperties @("EnumValue") `
    -ExpectedUnrealExitCode 20
$FailureResults += Invoke-RejectedScalarPatch `
    -CaseName "missing-property" `
    -PropertyName "DoesNotExist" `
    -Value 1 `
    -ExpectedRevision $ResetRevision `
    -AllowedProperties @("DoesNotExist") `
    -ExpectedUnrealExitCode 17
$FailureResults += Invoke-RejectedScalarPatch `
    -CaseName "dirty-package" `
    -PropertyName "BoolValue" `
    -Value $true `
    -ExpectedRevision $ResetRevision `
    -AllowedProperties @("BoolValue") `
    -TestFailureInjection "DirtyPackage" `
    -ExpectedUnrealExitCode 12
$SidecarPath = [System.IO.Path]::ChangeExtension($TargetPackagePath, ".uexp")
if (Test-Path -LiteralPath $SidecarPath)
{
    throw "Scalar failure regression refuses a pre-existing sidecar: $SidecarPath"
}
try
{
    [System.IO.File]::WriteAllBytes($SidecarPath, [byte[]](0x55, 0x45, 0x41, 0x4B))
    $FailureResults += Invoke-RejectedScalarPatch `
        -CaseName "sidecar-file" `
        -PropertyName "BoolValue" `
        -Value $true `
        -ExpectedRevision $ResetRevision `
        -AllowedProperties @("BoolValue") `
        -ExpectedUnrealExitCode 24
}
finally
{
    if (Test-Path -LiteralPath $SidecarPath)
    {
        Remove-Item -LiteralPath $SidecarPath -Force
    }
}
if (Test-Path -LiteralPath $SidecarPath)
{
    throw "Scalar failure regression could not remove its temporary sidecar: $SidecarPath"
}
$FailureResults += Invoke-RejectedScalarPatch `
    -CaseName "save-failure" `
    -PropertyName "BoolValue" `
    -Value $true `
    -ExpectedRevision $ResetRevision `
    -AllowedProperties @("BoolValue") `
    -Mode Commit `
    -TestFailureInjection "SaveFailure" `
    -ExpectedUnrealExitCode 21 `
    -ExpectBackup
$AfterFailures = Export-ScalarAsset -Directory (Join-Path $Output "AfterFailures")
Assert-Values -Canonical $AfterFailures -Expected $BaselineValues -Context "After expected failures"
if ($AfterFailures.revision.value -ne $ResetRevision)
{
    throw "Scalar fixture Revision changed during the expected failure matrix."
}

$Summary = [ordered]@{
    schemaVersion = "1.0"
    toolVersion = "0.5.0"
    projectPath = $ProjectPath
    assetPath = $AssetPath
    assetClass = $AssetClass
    fixturePlan = $FixturePlan
    initialRevision = $InitialRevision
    finalCommittedRevision = $CurrentRevision
    resetRevision = [string]$ResetCanonical.revision.value
    dryRunCount = $DryRunResults.Count
    commitCount = $CommitResults.Count
    failureCount = $FailureResults.Count
    finalResetVerified = $true
    failureMatrixDiskUnchanged = $true
    dryRuns = $DryRunResults
    commits = $CommitResults
    failures = $FailureResults
}
Write-Utf8Json -Path $SummaryPath -Value $Summary -Depth 30
Write-Host "Scalar patch regression completed."
Write-Host "Dry Runs : $($DryRunResults.Count)/$($Cases.Count)"
Write-Host "Commits  : $($CommitResults.Count)/$($Cases.Count)"
Write-Host "Failures : $($FailureResults.Count)/9 rejected with zero disk changes"
Write-Host "Summary  : $SummaryPath"
