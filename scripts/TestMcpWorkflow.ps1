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
$ProjectName = [System.IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$FixtureScript = Join-Path $PSScriptRoot "RunWriteFixturePlan.ps1"
$CatalogScript = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$AgentCmd = Join-Path $PSScriptRoot "ue-agent.cmd"
$ClientScript = Join-Path $ToolRoot "tests\integration\mcp_workflow_smoke.py"
$FixturePlan = Join-Path $ToolRoot "tests\fixtures\scalar_patch_regression_plan.json"
foreach ($Required in @($VenvPython, $FixtureScript, $CatalogScript, $AgentCmd, $ClientScript, $FixturePlan))
{
    Assert-UeakPath -Path $Required -Description ([System.IO.Path]::GetFileName($Required)) -PathType File
}

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output\McpWorkflowSmoke"
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

$BackupRoot = Join-Path $ToolRoot "Backups\McpWorkflowSmoke"
if (Test-Path -LiteralPath $BackupRoot)
{
    $Reparse = Get-ChildItem -LiteralPath $BackupRoot -Force -Recurse -ErrorAction Stop |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 } |
        Select-Object -First 1
    if ($null -ne $Reparse)
    {
        throw "BackupRoot contains a Junction or symbolic link: $($Reparse.FullName)"
    }
    Remove-Item -LiteralPath $BackupRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null

$FixtureDirectory = Join-Path $Output "Fixture"
$FixtureReport = Join-Path $FixtureDirectory "fixture-report.json"
$RevisionExport = Join-Path $Output "Revision"
$Database = Join-Path $Output "Index\ueak.sqlite3"
$Policy = Join-Path $Output "policy.json"
$WorkRoot = Join-Path $Output "Workflow"
$ErrorLog = Join-Path $Output "Logs\mcp-stderr.log"
$AssetPackage = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget"
$AssetPath = "$AssetPackage.DA_ScalarPatchTarget"
$AssetClass = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
$Succeeded = $false

function Write-Utf8Json
{
    param([string]$Path, [object]$Value)
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    $Json = $Value | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($Path, $Json + "`r`n", [System.Text.UTF8Encoding]::new($false))
}

try
{
    Write-Host "Resetting isolated scalar fixture..."
    & $FixtureScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Plan $FixturePlan `
        -Mode Reset `
        -Report $FixtureReport `
        -ValidationReport (Join-Path $FixtureDirectory "validation-report.json") `
        -VerificationOutput (Join-Path $FixtureDirectory "Reload") `
        -VerificationReport (Join-Path $FixtureDirectory "verification-report.json")
    if ($LASTEXITCODE -ne 0)
    {
        throw "Scalar fixture reset failed with exit code $LASTEXITCODE"
    }

    & $CatalogScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Asset $AssetPackage `
        -Output $RevisionExport
    if ($LASTEXITCODE -ne 0)
    {
        throw "Scalar Revision Export failed with exit code $LASTEXITCODE"
    }

    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Database)) -Force | Out-Null
    & $VenvPython (Join-Path $PSScriptRoot "ue-agent.py") index build $RevisionExport --database $Database --force --project-key $ProjectName | Out-Host
    if ($LASTEXITCODE -ne 0)
    {
        throw "MCP smoke index build failed with exit code $LASTEXITCODE"
    }
    foreach ($Suffix in @("-wal", "-shm", "-journal"))
    {
        if (Test-Path -LiteralPath ($Database + $Suffix))
        {
            throw "Index build left an active SQLite sidecar: $Database$Suffix"
        }
    }

    $PolicyValue = [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests/ScalarRegression")
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @("setAssetProperty")
        allowedAssetClasses = @($AssetClass)
        allowedAssetProperties = @("$AssetClass#BoolValue")
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    }
    Write-Utf8Json -Path $Policy -Value $PolicyValue

    $Fixture = [System.IO.File]::ReadAllText($FixtureReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $PackageFile = [string]$Fixture.fixtures[0].packageFilename
    Assert-UeakPath -Path $PackageFile -Description "scalar fixture package" -PathType File

    & $VenvPython $ClientScript `
        --engine-root $EngineRoot `
        --project $ProjectPath `
        --database $Database `
        --policy $Policy `
        --revision-export $RevisionExport `
        --work-root $WorkRoot `
        --backup-root $BackupRoot `
        --package-file $PackageFile `
        --error-log $ErrorLog
    if ($LASTEXITCODE -ne 0)
    {
        throw "Full MCP workflow smoke test failed with exit code $LASTEXITCODE"
    }
    $Succeeded = $true
}
finally
{
    if (!$Succeeded)
    {
        Write-Warning "MCP workflow did not complete; resetting the scalar fixture to a known baseline."
        try
        {
            & $FixtureScript `
                -EngineRoot $EngineRoot `
                -ProjectPath $ProjectPath `
                -Plan $FixturePlan `
                -Mode Reset `
                -Report (Join-Path $Output "Recovery\fixture-report.json") `
                -ValidationReport (Join-Path $Output "Recovery\validation-report.json") `
                -VerificationOutput (Join-Path $Output "Recovery\Reload") `
                -VerificationReport (Join-Path $Output "Recovery\verification-report.json") | Out-Host
        }
        catch
        {
            Write-Warning "Emergency fixture reset failed: $($_.Exception.Message)"
        }
    }
}

Write-Host "Full MCP workflow smoke test passed."
