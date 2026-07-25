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
    $Output = Join-Path $ToolRoot "Output\DataAssetReferenceProperties054"
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

$AssetPackage = "/Game/UEAgentKitWriteTests/DA_ReferencePatchTarget"
$AssetPath = "$AssetPackage.DA_ReferencePatchTarget"
$AssetClass = "/Script/UEAgentKitEditor.UEAgentKitReferenceWriteFixtureAsset"
$TexturePath = "/Game/Characters/Mannequins/Textures/Manny/T_Manny_02_D.T_Manny_02_D"
$TextureClass = "/Script/Engine.Texture2D"
$ActorClassPath = "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter.BP_ThirdPersonCharacter_C"
$ProjectName = [IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$ProjectDir = Split-Path -Parent $ProjectPath
$PackageFile = Join-Path $ProjectDir "Content\UEAgentKitWriteTests\DA_ReferencePatchTarget.uasset"

function Write-Json([string]$Path, [object]$Value)
{
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    [IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 40) + "`r`n"),
        [Text.UTF8Encoding]::new($false))
}

function Export-Asset([string]$Name)
{
    $Directory = Join-Path $Output $Name
    $Captured = & $RunCatalog -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Asset $AssetPackage -Output $Directory
    $Captured | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "Asset export $Name failed: $LASTEXITCODE" }
    $File = Get-ChildItem -LiteralPath (Join-Path $Directory "canonical") -Filter "*.json" -File -Recurse |
        Select-Object -First 1
    if ($null -eq $File) { throw "Canonical export missing for $Name" }
    $Canonical = [IO.File]::ReadAllText($File.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ([string]$Canonical.assetPath -ne $AssetPath) { throw "Unexpected exported asset: $($Canonical.assetPath)" }
    return [pscustomobject]@{ Root = $Directory; Value = $Canonical }
}

function Get-PropertyValue([object]$Canonical, [string]$Name)
{
    $Property = @($Canonical.assetDetails.properties) |
        Where-Object { [string]$_.name -eq $Name } |
        Select-Object -First 1
    if ($null -eq $Property) { throw "Data Asset export is missing property $Name" }
    return [string]$Property.value
}

function New-Policy()
{
    return [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests")
        allowedReferenceRoots = @(
            "/Game/Characters/Mannequins/Textures/Manny",
            "/Game/ThirdPerson/Blueprints"
        )
        allowedReferenceClasses = @($TextureClass, $ActorClassPath)
        allowedOperations = @("setAssetReferenceProperty")
        allowedAssetClasses = @($AssetClass)
        allowedAssetProperties = @(
            "$AssetClass#ObjectValue",
            "$AssetClass#ClassValue",
            "$AssetClass#SoftObjectValue",
            "$AssetClass#SoftClassValue"
        )
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    }
}

function Invoke-ReferencePatch(
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
        description = "Data Asset reference property regression: $PropertyName"
        assets = @([ordered]@{
            assetPath = $AssetPath
            expectedRevision = $ExpectedRevision
            expectedAssetClass = $AssetClass
            operations = @([ordered]@{
                operationId = $Id
                operation = "setAssetReferenceProperty"
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

function Invoke-ReferenceRollback([object]$Operation, [string]$Name)
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

$FixturePlan = Join-Path $Output "fixture-plan.json"
Write-Json $FixturePlan ([ordered]@{
    schemaVersion = "1.0"
    root = "/Game/UEAgentKitWriteTests"
    fixtures = @([ordered]@{
        id = "data-asset-reference-target"
        kind = "referenceAsset"
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
if ($LASTEXITCODE -ne 0) { throw "Reference Fixture reset failed: $LASTEXITCODE" }
Assert-UeakPath -Path $PackageFile -Description "Reference Data Asset fixture" -PathType File
$Emergency = Join-Path $Output "baseline.uasset"
Copy-Item -LiteralPath $PackageFile -Destination $Emergency -Force
$Restored = $false

try
{
    $Initial = Export-Asset "Initial"
    $InitialRevision = [string]$Initial.Value.revision.value
    foreach ($PropertyName in @("ObjectValue", "ClassValue", "SoftObjectValue", "SoftClassValue"))
    {
        if (![string]::IsNullOrEmpty((Get-PropertyValue $Initial.Value $PropertyName)))
        {
            throw "Fixture property $PropertyName is not initially empty"
        }
    }

    $ObjectValue = [ordered]@{ referenceType = "Object"; path = $TexturePath }
    $ObjectDry = Invoke-ReferencePatch "object-dry" "ObjectValue" $ObjectValue $InitialRevision $Initial.Root "DryRun"
    if ($ObjectDry.Report.saved -or !$ObjectDry.Report.rolledBack -or
        !$ObjectDry.Report.rollbackValueMatch -or !$ObjectDry.Report.diskUnchanged -or
        [string]$ObjectDry.Report.beforeRevision -ne [string]$ObjectDry.Report.afterRevision)
    {
        throw "Object Dry Run atomicity gates failed"
    }

    $ObjectManifest = Join-Path $Output "Backups\object\object.manifest.json"
    $Object = Invoke-ReferencePatch "object" "ObjectValue" $ObjectValue $InitialRevision $Initial.Root "Commit" $ObjectManifest
    $AfterObject = Export-Asset "AfterObject"
    if ((Get-PropertyValue $AfterObject.Value "ObjectValue") -ne $TexturePath) { throw "Object reference did not persist" }

    $NullDry = Invoke-ReferencePatch "object-null-dry" "ObjectValue" $null ([string]$AfterObject.Value.revision.value) $AfterObject.Root "DryRun"
    if (!$NullDry.Report.rolledBack -or !$NullDry.Report.rollbackValueMatch -or !$NullDry.Report.diskUnchanged)
    {
        throw "Null clear Dry Run did not restore the existing object reference"
    }

    $SoftObjectValue = [ordered]@{ referenceType = "SoftObject"; path = $TexturePath }
    $SoftObjectDry = Invoke-ReferencePatch "soft-object-dry" "SoftObjectValue" $SoftObjectValue ([string]$AfterObject.Value.revision.value) $AfterObject.Root "DryRun"
    if (!$SoftObjectDry.Report.rolledBack -or !$SoftObjectDry.Report.rollbackValueMatch -or !$SoftObjectDry.Report.diskUnchanged)
    {
        throw "Soft Object Dry Run atomicity gates failed"
    }

    $SoftObjectManifest = Join-Path $Output "Backups\soft-object\soft-object.manifest.json"
    $SoftObject = Invoke-ReferencePatch `
        "soft-object" `
        "SoftObjectValue" `
        $SoftObjectValue `
        ([string]$AfterObject.Value.revision.value) `
        $AfterObject.Root `
        "Commit" `
        $SoftObjectManifest
    $AfterSoftObject = Export-Asset "AfterSoftObject"

    $ClassValue = [ordered]@{ referenceType = "Class"; path = $ActorClassPath }
    $ClassDry = Invoke-ReferencePatch "class-dry" "ClassValue" $ClassValue ([string]$AfterSoftObject.Value.revision.value) $AfterSoftObject.Root "DryRun"
    if (!$ClassDry.Report.rolledBack -or !$ClassDry.Report.rollbackValueMatch -or !$ClassDry.Report.diskUnchanged)
    {
        throw "Class Dry Run atomicity gates failed"
    }

    $ClassManifest = Join-Path $Output "Backups\class\class.manifest.json"
    $Class = Invoke-ReferencePatch `
        "class" `
        "ClassValue" `
        $ClassValue `
        ([string]$AfterSoftObject.Value.revision.value) `
        $AfterSoftObject.Root `
        "Commit" `
        $ClassManifest
    $AfterClass = Export-Asset "AfterClass"

    $SoftClassValue = [ordered]@{ referenceType = "SoftClass"; path = $ActorClassPath }
    $SoftClassDry = Invoke-ReferencePatch "soft-class-dry" "SoftClassValue" $SoftClassValue ([string]$AfterClass.Value.revision.value) $AfterClass.Root "DryRun"
    if (!$SoftClassDry.Report.rolledBack -or !$SoftClassDry.Report.rollbackValueMatch -or !$SoftClassDry.Report.diskUnchanged)
    {
        throw "Soft Class Dry Run atomicity gates failed"
    }

    $SoftClassManifest = Join-Path $Output "Backups\soft-class\soft-class.manifest.json"
    $SoftClass = Invoke-ReferencePatch `
        "soft-class" `
        "SoftClassValue" `
        $SoftClassValue `
        ([string]$AfterClass.Value.revision.value) `
        $AfterClass.Root `
        "Commit" `
        $SoftClassManifest
    $Committed = Export-Asset "Committed"
    if ((Get-PropertyValue $Committed.Value "ObjectValue") -ne $TexturePath -or
        (Get-PropertyValue $Committed.Value "SoftObjectValue") -ne $TexturePath -or
        (Get-PropertyValue $Committed.Value "ClassValue") -ne $ActorClassPath -or
        (Get-PropertyValue $Committed.Value "SoftClassValue") -ne $ActorClassPath)
    {
        throw "Independent reload did not observe all four reference values"
    }

    Invoke-ReferenceRollback $SoftClass "soft-class"
    Invoke-ReferenceRollback $Class "class"
    Invoke-ReferenceRollback $SoftObject "soft-object"
    Invoke-ReferenceRollback $Object "object"

    $Final = Export-Asset "Final"
    if ([string]$Final.Value.revision.value -ne $InitialRevision) { throw "Final Revision mismatch" }
    foreach ($PropertyName in @("ObjectValue", "ClassValue", "SoftObjectValue", "SoftClassValue"))
    {
        if (![string]::IsNullOrEmpty((Get-PropertyValue $Final.Value $PropertyName)))
        {
            throw "Rollback did not clear $PropertyName"
        }
    }
    $Restored = $true
}
finally
{
    if (!$Restored -and @(Get-Process UnrealEditor,UnrealEditor-Cmd -ErrorAction SilentlyContinue).Count -eq 0)
    {
        Copy-Item -LiteralPath $Emergency -Destination $PackageFile -Force
        Write-Warning "Reference regression failed; raw baseline package restored"
    }
}

Write-Host "Data Asset reference property regression passed."
Write-Host "Asset=$AssetPath"
Write-Host "ReferenceTypes=Object,Class,SoftObject,SoftClass"
Write-Host "Revision=$InitialRevision"
