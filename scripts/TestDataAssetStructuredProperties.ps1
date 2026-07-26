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
$RunPatch = Join-Path $PSScriptRoot "RunPatch.ps1"
$RunRollback = Join-Path $PSScriptRoot "RunRollback.ps1"
foreach ($Required in @($RunFixture, $RunCatalog, $RunPatch, $RunRollback))
{
    Assert-UeakPath -Path $Required -Description ([IO.Path]::GetFileName($Required)) -PathType File
}

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output\DataAssetStructuredProperties054"
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

$AssetPackage = "/Game/UEAgentKitWriteTests/DA_StructuredPatchTarget"
$AssetPath = "$AssetPackage.DA_StructuredPatchTarget"
$AssetClass = "/Script/UEAgentKitEditor.UEAgentKitStructuredWriteFixtureAsset"
$ProjectName = [IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$ProjectDir = Split-Path -Parent $ProjectPath
$PackageFile = Join-Path $ProjectDir "Content\UEAgentKitWriteTests\DA_StructuredPatchTarget.uasset"

function Write-Json([string]$Path, [object]$Value)
{
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    [IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 80) + "`r`n"),
        [Text.UTF8Encoding]::new($false))
}

function ConvertTo-StableJson([object]$Value)
{
    return ($Value | ConvertTo-Json -Depth 80 -Compress)
}

function Export-Asset([string]$Name)
{
    $Directory = Join-Path $Output $Name
    $Captured = & $RunCatalog -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Asset $AssetPackage -Output $Directory
    $Captured | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "Asset export $Name failed: $LASTEXITCODE" }
    $File = Get-ChildItem -LiteralPath (Join-Path $Directory "canonical") -Filter "*.json" -File -Recurse |
        Where-Object { $_.Name -eq "DA_StructuredPatchTarget_DA_StructuredPatchTarget.json" } |
        Select-Object -First 1
    if ($null -eq $File) { throw "Canonical structured asset export missing for $Name" }
    $Canonical = [IO.File]::ReadAllText($File.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ([string]$Canonical.assetPath -ne $AssetPath) { throw "Unexpected exported asset: $($Canonical.assetPath)" }
    return [pscustomobject]@{ Root = $Directory; Value = $Canonical }
}

function Get-Property([object]$Canonical, [string]$Name)
{
    $Property = @($Canonical.assetDetails.properties) |
        Where-Object { [string]$_.name -eq $Name } |
        Select-Object -First 1
    if ($null -eq $Property) { throw "Data Asset export is missing property $Name" }
    return $Property
}

function Assert-StructuredValue([object]$Canonical, [string]$PropertyName, [object]$Expected)
{
    $ActualProperty = Get-Property $Canonical $PropertyName
    if (!$ActualProperty.structuredSupported) { throw "$PropertyName is not exported as a supported structured property" }
    $ActualJson = ConvertTo-StableJson $ActualProperty.value
    $ExpectedJson = ConvertTo-StableJson $Expected
    if ($ActualJson -cne $ExpectedJson)
    {
        throw "$PropertyName value mismatch. Expected=$ExpectedJson Actual=$ActualJson"
    }
}

function New-Policy()
{
    return [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests")
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @("setAssetStructuredProperty")
        allowedAssetClasses = @($AssetClass)
        allowedAssetProperties = @(
            "$AssetClass#StructValue",
            "$AssetClass#ArrayValue",
            "$AssetClass#SetValue",
            "$AssetClass#MapValue"
        )
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 65536
    }
}

function Invoke-StructuredPatch(
    [string]$Id,
    [string]$PropertyName,
    [object]$Value,
    [string]$ExpectedRevision,
    [string]$RevisionExport,
    [ValidateSet("DryRun", "Commit")][string]$Mode,
    [string]$Manifest = "")
{
    $PatchPath = Join-Path $Output "$Id.patch.json"
    $PolicyPath = Join-Path $Output "$Id.policy.json"
    $ReportPath = Join-Path $Output "$Id.$($Mode.ToLower()).report.json"
    $ValidationPath = Join-Path $Output "$Id.$($Mode.ToLower()).validation.json"
    $BackupRoot = Join-Path $Output "Backups\$Id"
    Write-Json $PolicyPath (New-Policy)
    Write-Json $PatchPath ([ordered]@{
        schemaVersion = "1.0"
        patchId = $Id
        projectName = $ProjectName
        description = "Data Asset structured property regression: $PropertyName"
        assets = @([ordered]@{
            assetPath = $AssetPath
            expectedRevision = $ExpectedRevision
            expectedAssetClass = $AssetClass
            operations = @([ordered]@{
                operationId = $Id
                operation = "setAssetStructuredProperty"
                target = [ordered]@{ propertyPath = $PropertyName }
                value = $Value
            })
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
    if ($LASTEXITCODE -ne 0) { throw "$PropertyName $Mode failed: $LASTEXITCODE" }
    $Report = [IO.File]::ReadAllText($ReportPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    return [pscustomobject]@{
        Report = $Report
        Policy = $PolicyPath
        BackupRoot = $BackupRoot
        Manifest = $Manifest
    }
}

function Assert-DryRun([object]$Operation, [string]$StructuredType, [string[]]$RequiredChanges)
{
    $Report = $Operation.Report
    if ($Report.saved -or !$Report.rolledBack -or !$Report.rollbackValueMatch -or !$Report.diskUnchanged -or
        [string]$Report.beforeRevision -ne [string]$Report.afterRevision)
    {
        throw "$StructuredType Dry Run atomicity gates failed"
    }
    if ([string]$Report.structuredType -ne $StructuredType -or [int]$Report.structuredDiffCount -le 0 -or
        $Report.structuredDiffTruncated)
    {
        throw "$StructuredType structured Diff metadata is invalid"
    }
    $Changes = @($Report.structuredDiff | ForEach-Object { [string]$_.change })
    foreach ($Required in $RequiredChanges)
    {
        if ($Changes -notcontains $Required)
        {
            throw "$StructuredType Diff is missing change kind $Required. Actual=$($Changes -join ',')"
        }
    }
}

function Invoke-StructuredRollback([object]$Operation, [string]$Name)
{
    $Report = Join-Path $Output "$Name.rollback.json"
    $VerifyRoot = Join-Path $Output "$Name.verify"
    $VerifyReport = Join-Path $Output "$Name.verify.json"
    & $RunRollback `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Manifest $Operation.Manifest `
        -Policy $Operation.Policy `
        -BackupRoot $Operation.BackupRoot `
        -Mode Commit `
        -Report $Report `
        -VerificationOutput $VerifyRoot `
        -VerificationReport $VerifyReport
    if ($LASTEXITCODE -ne 0) { throw "Rollback $Name failed: $LASTEXITCODE" }
    $Rollback = [IO.File]::ReadAllText($Report, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $Verification = [IO.File]::ReadAllText($VerifyReport, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if (!$Rollback.restored -or !$Verification.verified) { throw "Rollback $Name verification failed" }
}

$InitialStruct = [ordered]@{
    valueType = "Struct"
    fields = [ordered]@{ bEnabled = $true; Count = 1; Label = "Initial" }
}
$InitialArray = [ordered]@{ valueType = "Array"; items = @(1, 2, 3) }
$InitialSet = [ordered]@{ valueType = "Set"; items = @("Alpha", "Beta") }
$InitialMap = [ordered]@{
    valueType = "Map"
    entries = @(
        [ordered]@{
            key = "Primary"
            value = [ordered]@{
                valueType = "Struct"
                fields = [ordered]@{ bEnabled = $true; Count = 10; Label = "Primary" }
            }
        },
        [ordered]@{
            key = "Secondary"
            value = [ordered]@{
                valueType = "Struct"
                fields = [ordered]@{ bEnabled = $false; Count = 20; Label = "Secondary" }
            }
        }
    )
}

$StructValue = [ordered]@{
    valueType = "Struct"
    fields = [ordered]@{ bEnabled = $false; Count = 7; Label = "Updated" }
}
$ArrayValue = [ordered]@{ valueType = "Array"; items = @(1, 4, 9, 16) }
$SetValue = [ordered]@{ valueType = "Set"; items = @("Alpha", "Gamma") }
$MapValue = [ordered]@{
    valueType = "Map"
    entries = @(
        [ordered]@{
            key = "Primary"
            value = [ordered]@{
                valueType = "Struct"
                fields = [ordered]@{ bEnabled = $false; Count = 11; Label = "Primary Updated" }
            }
        },
        [ordered]@{
            key = "Tertiary"
            value = [ordered]@{
                valueType = "Struct"
                fields = [ordered]@{ bEnabled = $true; Count = 30; Label = "Tertiary" }
            }
        }
    )
}

$FixturePlan = Join-Path $Output "fixture-plan.json"
Write-Json $FixturePlan ([ordered]@{
    schemaVersion = "1.0"
    root = "/Game/UEAgentKitWriteTests"
    fixtures = @([ordered]@{
        id = "data-asset-structured-target"
        kind = "structuredAsset"
        targetAsset = $AssetPackage
        expectedClass = $AssetClass
    })
})
& $RunFixture `
    -EngineRoot $EngineRoot `
    -ProjectPath $ProjectPath `
    -Plan $FixturePlan `
    -Mode Reset `
    -Report (Join-Path $Output "fixture-report.json") `
    -ValidationReport (Join-Path $Output "fixture-validation.json") `
    -VerificationOutput (Join-Path $Output "fixture-reload") `
    -VerificationReport (Join-Path $Output "fixture-verification.json")
if ($LASTEXITCODE -ne 0) { throw "Structured Fixture reset failed: $LASTEXITCODE" }
Assert-UeakPath -Path $PackageFile -Description "Structured Data Asset fixture" -PathType File
$Emergency = Join-Path $Output "baseline.uasset"
Copy-Item -LiteralPath $PackageFile -Destination $Emergency -Force
$Restored = $false

try
{
    $Initial = Export-Asset "Initial"
    $InitialRevision = [string]$Initial.Value.revision.value
    if ([int]$Initial.Value.assetDetails.readerVersion -ne 2) { throw "Data Asset readerVersion is not 2" }
    Assert-StructuredValue $Initial.Value "StructValue" $InitialStruct
    Assert-StructuredValue $Initial.Value "ArrayValue" $InitialArray
    Assert-StructuredValue $Initial.Value "SetValue" $InitialSet
    Assert-StructuredValue $Initial.Value "MapValue" $InitialMap

    $StructDry = Invoke-StructuredPatch "struct-dry" "StructValue" $StructValue $InitialRevision $Initial.Root "DryRun"
    Assert-DryRun $StructDry "Struct" @("replace")
    $StructManifest = Join-Path $Output "Backups\struct\struct.manifest.json"
    $Struct = Invoke-StructuredPatch "struct" "StructValue" $StructValue $InitialRevision $Initial.Root "Commit" $StructManifest
    $AfterStruct = Export-Asset "AfterStruct"
    Assert-StructuredValue $AfterStruct.Value "StructValue" $StructValue

    $ArrayDry = Invoke-StructuredPatch "array-dry" "ArrayValue" $ArrayValue ([string]$AfterStruct.Value.revision.value) $AfterStruct.Root "DryRun"
    Assert-DryRun $ArrayDry "Array" @("replace", "array-add")
    $ArrayManifest = Join-Path $Output "Backups\array\array.manifest.json"
    $Array = Invoke-StructuredPatch "array" "ArrayValue" $ArrayValue ([string]$AfterStruct.Value.revision.value) $AfterStruct.Root "Commit" $ArrayManifest
    $AfterArray = Export-Asset "AfterArray"
    Assert-StructuredValue $AfterArray.Value "ArrayValue" $ArrayValue

    $SetDry = Invoke-StructuredPatch "set-dry" "SetValue" $SetValue ([string]$AfterArray.Value.revision.value) $AfterArray.Root "DryRun"
    Assert-DryRun $SetDry "Set" @("set-remove", "set-add")
    $SetManifest = Join-Path $Output "Backups\set\set.manifest.json"
    $Set = Invoke-StructuredPatch "set" "SetValue" $SetValue ([string]$AfterArray.Value.revision.value) $AfterArray.Root "Commit" $SetManifest
    $AfterSet = Export-Asset "AfterSet"
    Assert-StructuredValue $AfterSet.Value "SetValue" $SetValue

    $MapDry = Invoke-StructuredPatch "map-dry" "MapValue" $MapValue ([string]$AfterSet.Value.revision.value) $AfterSet.Root "DryRun"
    Assert-DryRun $MapDry "Map" @("replace", "map-remove", "map-add")
    $MapManifest = Join-Path $Output "Backups\map\map.manifest.json"
    $Map = Invoke-StructuredPatch "map" "MapValue" $MapValue ([string]$AfterSet.Value.revision.value) $AfterSet.Root "Commit" $MapManifest
    $Committed = Export-Asset "Committed"
    Assert-StructuredValue $Committed.Value "StructValue" $StructValue
    Assert-StructuredValue $Committed.Value "ArrayValue" $ArrayValue
    Assert-StructuredValue $Committed.Value "SetValue" $SetValue
    Assert-StructuredValue $Committed.Value "MapValue" $MapValue

    Invoke-StructuredRollback $Map "map"
    Invoke-StructuredRollback $Set "set"
    Invoke-StructuredRollback $Array "array"
    Invoke-StructuredRollback $Struct "struct"

    $Final = Export-Asset "Final"
    if ([string]$Final.Value.revision.value -ne $InitialRevision) { throw "Final Revision mismatch" }
    Assert-StructuredValue $Final.Value "StructValue" $InitialStruct
    Assert-StructuredValue $Final.Value "ArrayValue" $InitialArray
    Assert-StructuredValue $Final.Value "SetValue" $InitialSet
    Assert-StructuredValue $Final.Value "MapValue" $InitialMap
    $Restored = $true
}
finally
{
    if (!$Restored -and @(Get-Process UnrealEditor,UnrealEditor-Cmd -ErrorAction SilentlyContinue).Count -eq 0)
    {
        Copy-Item -LiteralPath $Emergency -Destination $PackageFile -Force
        Write-Warning "Structured regression failed; raw baseline package restored"
    }
}

Write-Host "Data Asset structured property regression passed."
Write-Host "Asset=$AssetPath"
Write-Host "StructuredTypes=Struct,Array,Set,Map"
Write-Host "Revision=$InitialRevision"
