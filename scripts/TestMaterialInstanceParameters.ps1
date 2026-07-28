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
    $Output = Join-Path $ToolRoot "Output\MaterialInstanceParameters05x"
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
$AssetClass = "/Script/Engine.MaterialInstanceConstant"
$TextureRoot = "/Game/Characters/Mannequins/Textures/Manny"
$TextureClass = "/Script/Engine.Texture2D"

$Cases = @(
    [ordered]@{
        Id = "scalar"
        Type = "Scalar"
        Operation = "setMaterialInstanceScalarParameter"
        Parameter = "Roughness"
        Package = "/Game/UEAgentKitWriteTests/MI_PatchTarget"
        Object = "MI_PatchTarget"
        Source = "/Game/LevelPrototyping/Materials/MI_DefaultColorway.MI_DefaultColorway"
    },
    [ordered]@{
        Id = "vector"
        Type = "Vector"
        Operation = "setMaterialInstanceVectorParameter"
        Parameter = "Base Color"
        Package = "/Game/UEAgentKitWriteTests/MI_PatchTarget"
        Object = "MI_PatchTarget"
        Source = "/Game/LevelPrototyping/Materials/MI_DefaultColorway.MI_DefaultColorway"
    },
    [ordered]@{
        Id = "texture"
        Type = "Texture"
        Operation = "setMaterialInstanceTextureParameter"
        Parameter = "Base Texture"
        Package = "/Game/UEAgentKitWriteTests/MI_TexturePatchTarget"
        Object = "MI_TexturePatchTarget"
        Source = "/Game/Characters/Mannequins/Materials/Manny/MI_Manny_01_New.MI_Manny_01_New"
    },
    [ordered]@{
        Id = "static-switch"
        Type = "StaticSwitch"
        Operation = "setMaterialInstanceStaticSwitchParameter"
        Parameter = "Logo?"
        Package = "/Game/UEAgentKitWriteTests/MI_StaticSwitchPatchTarget"
        Object = "MI_StaticSwitchPatchTarget"
        Source = "/Game/Characters/Mannequins/Materials/Manny/MI_Manny_02_New.MI_Manny_02_New"
    }
)
foreach ($Case in $Cases)
{
    $Case["AssetPath"] = "$($Case.Package).$($Case.Object)"
    $Case["PackageFile"] = Join-Path $ProjectDir ("Content" + $Case.Package.Substring(5).Replace("/", "\") + ".uasset")
}

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

function Export-Asset([object]$Case, [string]$Name)
{
    $Directory = Join-Path $Output "$($Case.Id)\$Name"
    $Captured = & $RunCatalog -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Asset $Case.Package -Output $Directory
    $Captured | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "$($Case.Id) export $Name failed: $LASTEXITCODE" }
    $ExpectedName = "$($Case.Object)_$($Case.Object).json"
    $File = Get-ChildItem -LiteralPath (Join-Path $Directory "canonical") -Filter $ExpectedName -File -Recurse |
        Select-Object -First 1
    if ($null -eq $File) { throw "$($Case.Id) canonical export missing for $Name" }
    $Canonical = [IO.File]::ReadAllText($File.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ([string]$Canonical.assetPath -ne [string]$Case.AssetPath)
    {
        throw "$($Case.Id) exported unexpected asset: $($Canonical.assetPath)"
    }
    if ([int]$Canonical.assetDetails.readerVersion -ne 2)
    {
        throw "$($Case.Id) Material Instance Reader version is not 2"
    }
    return [pscustomobject]@{ Root = $Directory; Value = $Canonical }
}

function Get-Parameter([object]$Canonical, [object]$Case)
{
    $CollectionName = switch ([string]$Case.Type)
    {
        "Scalar" { "scalarParameters" }
        "Vector" { "vectorParameters" }
        "Texture" { "textureParameters" }
        "StaticSwitch" { "staticSwitchParameters" }
        default { throw "Unknown material parameter type: $($Case.Type)" }
    }
    $Parameter = @($Canonical.assetDetails.$CollectionName) |
        Where-Object { [string]$_.name -eq [string]$Case.Parameter } |
        Select-Object -First 1
    if ($null -eq $Parameter) { throw "$($Case.Id) parameter is missing: $($Case.Parameter)" }
    if (!$Parameter.override) { throw "$($Case.Id) parameter is not an override" }
    if ([string]::IsNullOrWhiteSpace([string]$Parameter.expressionGuid))
    {
        throw "$($Case.Id) parameter expressionGuid is missing"
    }
    return $Parameter
}

function Get-ParameterValue([object]$Parameter, [object]$Case)
{
    if ([string]$Case.Type -eq "Texture") { return [string]$Parameter.valuePath }
    return $Parameter.value
}

function Assert-ValueEqual([object]$Actual, [object]$Expected, [object]$Case, [string]$Stage)
{
    switch ([string]$Case.Type)
    {
        "Scalar"
        {
            if ([Math]::Abs([double]$Actual - [double]$Expected) -gt 0.00001)
            {
                throw "$($Case.Id) $Stage scalar mismatch. Expected=$Expected Actual=$Actual"
            }
        }
        "Vector"
        {
            foreach ($Channel in @("r", "g", "b", "a"))
            {
                if ([Math]::Abs([double]$Actual.$Channel - [double]$Expected.$Channel) -gt 0.00001)
                {
                    throw "$($Case.Id) $Stage vector $Channel mismatch"
                }
            }
        }
        "Texture"
        {
            if ([string]$Actual -cne [string]$Expected)
            {
                throw "$($Case.Id) $Stage texture mismatch. Expected=$Expected Actual=$Actual"
            }
        }
        "StaticSwitch"
        {
            if ([bool]$Actual -ne [bool]$Expected)
            {
                throw "$($Case.Id) $Stage static switch mismatch. Expected=$Expected Actual=$Actual"
            }
        }
    }
}

function New-CaseValue([object]$Before, [object]$Case)
{
    switch ([string]$Case.Type)
    {
        "Scalar"
        {
            if ([Math]::Abs([double]$Before - 0.73) -lt 0.00001) { return 0.27 }
            return 0.73
        }
        "Vector"
        {
            $Candidate = [ordered]@{ r = 0.13; g = 0.37; b = 0.79; a = 1.0 }
            if ([Math]::Abs([double]$Before.r - 0.13) -lt 0.00001 -and
                [Math]::Abs([double]$Before.g - 0.37) -lt 0.00001 -and
                [Math]::Abs([double]$Before.b - 0.79) -lt 0.00001)
            {
                return [ordered]@{ r = 0.81; g = 0.29; b = 0.17; a = 1.0 }
            }
            return $Candidate
        }
        "Texture"
        {
            $First = "$TextureRoot/T_Manny_01_D.T_Manny_01_D"
            $Second = "$TextureRoot/T_Manny_02_D.T_Manny_02_D"
            if ([string]$Before -eq $Second) { return $First }
            return $Second
        }
        "StaticSwitch" { return ![bool]$Before }
    }
}

function New-Policy([object]$Case)
{
    $ReferenceRoots = @()
    $ReferenceClasses = @()
    if ([string]$Case.Type -eq "Texture")
    {
        $ReferenceRoots = @($TextureRoot)
        $ReferenceClasses = @($TextureClass)
    }
    return [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests")
        allowedReferenceRoots = $ReferenceRoots
        allowedReferenceClasses = $ReferenceClasses
        allowedOperations = @([string]$Case.Operation)
        allowedAssetClasses = @($AssetClass)
        allowedAssetProperties = @()
        allowedMaterialParameters = @("$AssetClass#$($Case.Type)#$($Case.Parameter)")
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    }
}

function Invoke-CasePatch(
    [object]$Case,
    [string]$Name,
    [object]$Value,
    [string]$ExpectedRevision,
    [string]$RevisionExport,
    [ValidateSet("DryRun", "Commit")][string]$Mode,
    [string]$Manifest = "")
{
    $Directory = Join-Path $Output "$($Case.Id)\$Name"
    $PatchPath = Join-Path $Directory "patch.json"
    $PolicyPath = Join-Path $Directory "policy.json"
    $ReportPath = Join-Path $Directory "report.json"
    $ValidationPath = Join-Path $Directory "validation.json"
    $BackupRoot = Join-Path $Directory "Backups"
    Write-Json $PolicyPath (New-Policy $Case)
    Write-Json $PatchPath ([ordered]@{
        schemaVersion = "1.0"
        patchId = "material-$($Case.Id)-$Name"
        projectName = $ProjectName
        description = "Material Instance $($Case.Type) report regression"
        assets = @([ordered]@{
            assetPath = $Case.AssetPath
            expectedRevision = $ExpectedRevision
            expectedAssetClass = $AssetClass
            operations = @([ordered]@{
                operationId = "set-$($Case.Id)"
                operation = $Case.Operation
                target = [ordered]@{ parameterName = $Case.Parameter }
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
    if ($LASTEXITCODE -ne 0) { throw "$($Case.Id) $Mode failed: $LASTEXITCODE" }
    $Report = [IO.File]::ReadAllText($ReportPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    return [pscustomobject]@{
        Report = $Report
        Policy = $PolicyPath
        BackupRoot = $BackupRoot
        Manifest = $Manifest
    }
}

function Assert-Report(
    [object]$Report,
    [object]$Case,
    [object]$BeforeParameter,
    [object]$BeforeValue,
    [object]$NewValue,
    [bool]$DryRun)
{
    if ([string]$Report.targetType -ne "MaterialInstanceParameter") { throw "$($Case.Id) targetType mismatch" }
    if ([string]$Report.targetDescription -ne "material-instance-parameter:$($Case.Type):$($Case.Parameter)")
    {
        throw "$($Case.Id) targetDescription mismatch"
    }
    if ([string]$Report.parameterName -ne [string]$Case.Parameter -or
        [string]$Report.parameterType -ne [string]$Case.Type -or
        [string]$Report.parameterAssociation -ne "Global")
    {
        throw "$($Case.Id) top-level parameter identity mismatch"
    }
    if ([string]$Report.materialParameter.name -ne [string]$Case.Parameter -or
        [string]$Report.materialParameter.type -ne [string]$Case.Type -or
        [string]$Report.materialParameter.association -ne "Global")
    {
        throw "$($Case.Id) nested parameter identity mismatch"
    }
    if (!$Report.beforeOverride -or !$Report.afterOverride -or !$Report.restoredOverride)
    {
        throw "$($Case.Id) override state mismatch"
    }
    $ExpectedGuid = [string]$BeforeParameter.expressionGuid
    foreach ($Guid in @(
        [string]$Report.beforeExpressionGuid,
        [string]$Report.afterExpressionGuid,
        [string]$Report.restoredExpressionGuid))
    {
        if ($Guid -ne $ExpectedGuid) { throw "$($Case.Id) expressionGuid mismatch" }
    }
    Assert-ValueEqual $Report.beforeValue $BeforeValue $Case "report before"
    Assert-ValueEqual $Report.afterValue $NewValue $Case "report after"
    Assert-ValueEqual $Report.materialParameter.before.value $BeforeValue $Case "nested before"
    Assert-ValueEqual $Report.materialParameter.after.value $NewValue $Case "nested after"
    if (!$Report.materialParameter.change.changed -or !$Report.materialParameter.change.valueChanged)
    {
        throw "$($Case.Id) structured change was not reported"
    }
    if ($DryRun)
    {
        Assert-ValueEqual $Report.restoredValue $BeforeValue $Case "report restored"
        if ($Report.saved -or !$Report.rolledBack -or
            !$Report.rollbackValueMatch -or !$Report.rollbackMetadataMatch -or
            !$Report.rollbackStateMatch -or !$Report.rollbackStructureMatch -or
            !$Report.diskUnchanged)
        {
            throw "$($Case.Id) Dry Run atomicity gates failed"
        }
        if ($Report.materialParameter.rollbackChange.changed)
        {
            throw "$($Case.Id) Dry Run rollbackChange is not empty"
        }
        if ([string]$Report.beforeRevision -ne [string]$Report.afterRevision)
        {
            throw "$($Case.Id) Dry Run changed disk revision"
        }
    }
    elseif (!$Report.saved -or $Report.rolledBack)
    {
        throw "$($Case.Id) Commit state mismatch"
    }
}

function Invoke-CaseRollback([object]$Case, [object]$Operation)
{
    $Directory = Join-Path $Output "$($Case.Id)\rollback"
    $Report = Join-Path $Directory "rollback.json"
    $VerifyRoot = Join-Path $Directory "verify"
    $VerifyReport = Join-Path $Directory "verify.json"
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
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
    if ($LASTEXITCODE -ne 0) { throw "$($Case.Id) rollback failed: $LASTEXITCODE" }
    $Rollback = [IO.File]::ReadAllText($Report, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $Verification = [IO.File]::ReadAllText($VerifyReport, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if (!$Rollback.restored -or !$Verification.verified) { throw "$($Case.Id) rollback verification failed" }
}

$FixturePlan = Join-Path $Output "fixture-plan.json"
Write-Json $FixturePlan ([ordered]@{
    schemaVersion = "1.0"
    root = "/Game/UEAgentKitWriteTests"
    fixtures = @(
        [ordered]@{
            id = "material-value-target"
            kind = "duplicateAsset"
            sourceAsset = $Cases[0].Source
            targetAsset = $Cases[0].Package
            expectedClass = $AssetClass
        },
        [ordered]@{
            id = "material-texture-target"
            kind = "duplicateAsset"
            sourceAsset = $Cases[2].Source
            targetAsset = $Cases[2].Package
            expectedClass = $AssetClass
        },
        [ordered]@{
            id = "material-static-switch-target"
            kind = "duplicateAsset"
            sourceAsset = $Cases[3].Source
            targetAsset = $Cases[3].Package
            expectedClass = $AssetClass
        }
    )
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
if ($LASTEXITCODE -ne 0) { throw "Material fixture reset failed: $LASTEXITCODE" }

$Emergency = @{}
foreach ($PackageFile in @($Cases | ForEach-Object { [string]$_.PackageFile } | Select-Object -Unique))
{
    Assert-UeakPath -Path $PackageFile -Description "Material fixture package" -PathType File
    $Backup = Join-Path $Output ("baseline-" + [IO.Path]::GetFileName($PackageFile))
    Copy-Item -LiteralPath $PackageFile -Destination $Backup -Force
    $Emergency[$PackageFile] = $Backup
}
$Restored = $false

try
{
    foreach ($Case in $Cases)
    {
        $Initial = Export-Asset $Case "Initial"
        $InitialRevision = [string]$Initial.Value.revision.value
        $BeforeParameter = Get-Parameter $Initial.Value $Case
        $BeforeValue = Get-ParameterValue $BeforeParameter $Case
        $NewValue = New-CaseValue $BeforeValue $Case

        $Dry = Invoke-CasePatch $Case "dryrun" $NewValue $InitialRevision $Initial.Root "DryRun"
        Assert-Report $Dry.Report $Case $BeforeParameter $BeforeValue $NewValue $true

        $Manifest = Join-Path $Output "$($Case.Id)\commit\Backups\$($Case.Id).manifest.json"
        $Commit = Invoke-CasePatch $Case "commit" $NewValue $InitialRevision $Initial.Root "Commit" $Manifest
        Assert-Report $Commit.Report $Case $BeforeParameter $BeforeValue $NewValue $false

        $After = Export-Asset $Case "AfterCommit"
        $AfterParameter = Get-Parameter $After.Value $Case
        Assert-ValueEqual (Get-ParameterValue $AfterParameter $Case) $NewValue $Case "independent reload"
        if ([string]$AfterParameter.expressionGuid -ne [string]$Commit.Report.afterExpressionGuid -or
            !$AfterParameter.override)
        {
            throw "$($Case.Id) independent reload metadata mismatch"
        }

        Invoke-CaseRollback $Case $Commit
        $Final = Export-Asset $Case "Final"
        if ([string]$Final.Value.revision.value -ne $InitialRevision)
        {
            throw "$($Case.Id) final Revision mismatch"
        }
        $FinalParameter = Get-Parameter $Final.Value $Case
        Assert-ValueEqual (Get-ParameterValue $FinalParameter $Case) $BeforeValue $Case "rollback"
        if ([string]$FinalParameter.expressionGuid -ne [string]$BeforeParameter.expressionGuid -or
            [bool]$FinalParameter.override -ne [bool]$BeforeParameter.override)
        {
            throw "$($Case.Id) rollback metadata mismatch"
        }
    }
    $Restored = $true
}
finally
{
    if (!$Restored -and @(Get-Process UnrealEditor,UnrealEditor-Cmd -ErrorAction SilentlyContinue).Count -eq 0)
    {
        foreach ($Entry in $Emergency.GetEnumerator())
        {
            Copy-Item -LiteralPath $Entry.Value -Destination $Entry.Key -Force
        }
        Write-Warning "Material regression failed; raw baseline packages restored"
    }
}

Write-Host "Material Instance parameter regression passed."
Write-Host "ParameterTypes=Scalar,Vector,Texture,StaticSwitch"
Write-Host "ReportContract=materialParameter-v1"
