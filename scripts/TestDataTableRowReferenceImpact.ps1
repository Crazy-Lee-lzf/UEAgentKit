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
if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output\DataTableRowReferenceImpact"
}
$Output = [System.IO.Path]::GetFullPath($Output)
$SafeRoot = [System.IO.Path]::GetFullPath((Join-Path $ToolRoot "Output"))
if (!$Output.StartsWith($SafeRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase))
{
    throw "Unsafe Output: $Output"
}
if (Test-Path $Output)
{
    Remove-Item $Output -Recurse -Force
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$ProjectName = [System.IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$ProjectDir = Split-Path -Parent $ProjectPath
$ProjectLog = Join-Path $ProjectDir "Saved\Logs\$ProjectName.log"
$TargetPackage = "/Game/UEAgentKitTests/DT_SearchableNameFixture"
$TargetAssetPath = "$TargetPackage.DT_SearchableNameFixture"
$SourcePackage = "/Game/UEAgentKitTests/BP_SearchableNameFixture"
$SourceAssetPath = "$SourcePackage.BP_SearchableNameFixture"
$AssetClass = "/Script/Engine.DataTable"
$RowName = "Row_Alpha"
$ExpectedTargetPath = "$TargetAssetPath::$RowName"
$ExpectedTargetSymbolId = "searchable-name|$ExpectedTargetPath"
$ExpectedError = "DataTable row is referenced and cannot be removed or renamed."

function Write-Json([string]$Path, [object]$Value)
{
    [System.IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 40) + "`r`n"),
        [System.Text.UTF8Encoding]::new($false))
}

function Export-One([string]$Name, [string]$Asset, [switch]$IncludeBlueprints)
{
    $Directory = Join-Path $Output $Name
    $Arguments = @{
        EngineRoot = $EngineRoot
        ProjectPath = $ProjectPath
        Asset = $Asset
        Output = $Directory
    }
    if ($IncludeBlueprints)
    {
        $Arguments.IncludeBlueprints = $true
    }
    $Captured = & $RunAssetCatalog @Arguments
    $Captured | Select-Object -Last 1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0)
    {
        throw "Export $Name failed: $LASTEXITCODE"
    }
    $CanonicalFile = Get-ChildItem (Join-Path $Directory "canonical") -Filter *.json -File -Recurse | Select-Object -First 1
    if (!$CanonicalFile)
    {
        throw "Export $Name produced no canonical JSON."
    }
    return [pscustomobject]@{
        Root = $Directory
        Value = ([System.IO.File]::ReadAllText($CanonicalFile.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json)
    }
}

function Make-Policy()
{
    return [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitTests")
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @("removeDataTableRow", "renameDataTableRow")
        allowedAssetClasses = @($AssetClass)
        allowedAssetProperties = @()
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    }
}

function Invoke-ExpectedRejection(
    [string]$Id,
    [string]$Operation,
    [hashtable]$Target,
    [string]$Revision,
    [string]$RevisionExport)
{
    $PatchPath = Join-Path $Output "$Id.patch.json"
    $PolicyPath = Join-Path $Output "$Id.policy.json"
    $ReportPath = Join-Path $Output "$Id.report.json"
    $ValidationPath = Join-Path $Output "$Id.validation.json"
    $BackupDir = Join-Path $Output "Backups\$Id"
    $StdoutPath = Join-Path $Output "$Id.stdout.log"
    $StderrPath = Join-Path $Output "$Id.stderr.log"

    Write-Json $PolicyPath (Make-Policy)
    Write-Json $PatchPath ([ordered]@{
        schemaVersion = "1.0"
        patchId = "data-table-row-reference-impact-$Id"
        projectName = $ProjectName
        description = "Verify exact DataTable row reference impact rejection."
        assets = @([ordered]@{
            assetPath = $TargetAssetPath
            expectedRevision = $Revision
            expectedAssetClass = $AssetClass
            operations = @([ordered]@{
                operationId = $Id
                operation = $Operation
                target = $Target
                value = $true
            })
        })
    })

    $ProcessArguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $RunPatch,
        "-EngineRoot", $EngineRoot,
        "-ProjectPath", $ProjectPath,
        "-Patch", $PatchPath,
        "-Policy", $PolicyPath,
        "-RevisionExport", $RevisionExport,
        "-Mode", "DryRun",
        "-Report", $ReportPath,
        "-ValidationReport", $ValidationPath,
        "-BackupDir", $BackupDir)
    $Process = Start-Process -FilePath "powershell.exe" -ArgumentList $ProcessArguments -Wait -PassThru -NoNewWindow -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath
    if ($Process.ExitCode -eq 0)
    {
        throw "$Operation unexpectedly succeeded for referenced row $RowName."
    }
    if (!(Test-Path $ValidationPath))
    {
        throw "$Operation produced no validation report."
    }
    $Validation = [System.IO.File]::ReadAllText($ValidationPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if (!$Validation.valid -or !$Validation.commitAllowedByPolicy)
    {
        throw "$Operation was rejected before the UE reference-impact gate."
    }
    if (!(Test-Path $ProjectLog))
    {
        throw "Project log not found after ${Operation}: $ProjectLog"
    }
    $LogText = [System.IO.File]::ReadAllText($ProjectLog, [System.Text.Encoding]::UTF8)
    if (!$LogText.Contains($ExpectedError) -or !$LogText.Contains("ReferenceCount=1") -or !$LogText.Contains("result 17"))
    {
        throw "$Operation did not report the expected exact reference-impact rejection."
    }
}

$Initial = Export-One "Initial" $TargetPackage
$Source = Export-One "Source" $SourcePackage -IncludeBlueprints

if ([string]$Initial.Value.assetPath -ne $TargetAssetPath -or [string]$Initial.Value.assetClass -ne $AssetClass)
{
    throw "Unexpected target fixture identity."
}
$InitialRows = @($Initial.Value.assetDetails.rows | ForEach-Object { [string]$_.Name } | Sort-Object)
if ($InitialRows.Count -ne 1 -or $InitialRows[0] -ne $RowName)
{
    throw "Unexpected initial rows: $($InitialRows -join ',')"
}
$InitialRevision = [string]$Initial.Value.revision.value
if ([string]::IsNullOrWhiteSpace($InitialRevision))
{
    throw "Target fixture has no Revision."
}

$ExactReferences = @($Source.Value.references | Where-Object {
    [string]$_.kind -eq "depends-searchable-name" -and
    [string]$_.targetKind -eq "searchable-name" -and
    [string]$_.targetAssetPath -eq $TargetAssetPath -and
    [string]$_.targetPath -eq $ExpectedTargetPath -and
    [string]$_.targetSymbolId -eq $ExpectedTargetSymbolId -and
    [string]$_.targetValueName -eq $RowName
})
if ($ExactReferences.Count -ne 1 -or [string]$Source.Value.assetPath -ne $SourceAssetPath)
{
    throw "Expected one exact Searchable Name reference for $ExpectedTargetPath."
}

Invoke-ExpectedRejection "remove-referenced-row" "removeDataTableRow" ([ordered]@{ rowName = $RowName }) $InitialRevision $Initial.Root
Invoke-ExpectedRejection "rename-referenced-row" "renameDataTableRow" ([ordered]@{ rowName = $RowName; newRowName = "Row_Beta" }) $InitialRevision $Initial.Root

$After = Export-One "AfterRejectedOperations" $TargetPackage
$AfterRows = @($After.Value.assetDetails.rows | ForEach-Object { [string]$_.Name } | Sort-Object)
$AfterRevision = [string]$After.Value.revision.value
if ($AfterRevision -ne $InitialRevision -or $AfterRows.Count -ne 1 -or $AfterRows[0] -ne $RowName)
{
    throw "Rejected operations changed the DataTable. Revision=$AfterRevision Rows=$($AfterRows -join ',')"
}

Write-Host "DataTable row reference-impact regression passed."
Write-Host "Target=$TargetAssetPath"
Write-Host "Row=$RowName"
Write-Host "Referencer=$SourceAssetPath"
Write-Host "Revision=$AfterRevision"
