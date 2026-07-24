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
$ClientScript = Join-Path $ToolRoot "tests\integration\mcp_snapshot_refresh_smoke.py"
$FixturePlan = Join-Path $ToolRoot "tests\fixtures\scalar_patch_regression_plan.json"
foreach ($Required in @($VenvPython, $FixtureScript, $CatalogScript, $ClientScript, $FixturePlan))
{
    Assert-UeakPath -Path $Required -Description ([System.IO.Path]::GetFileName($Required)) -PathType File
}

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output\McpSnapshotRefreshSmoke"
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

$BackupRoot = Join-Path $ToolRoot "Backups\McpSnapshotRefreshSmoke"
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
$ErrorLogFirst = Join-Path $Output "Logs\mcp-first-stderr.log"
$ErrorLogSecond = Join-Path $Output "Logs\mcp-second-stderr.log"
$AssetPackage = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget"
$AssetClass = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
$BaselineHash = ""
$BaselineCopy = Join-Path $FixtureDirectory "package-baseline.uasset"
$PackageFile = ""

function Write-Utf8Json
{
    param([string]$Path, [object]$Value)
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    $Json = $Value | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($Path, $Json + "`r`n", [System.Text.UTF8Encoding]::new($false))
}

function Reset-ScalarFixture
{
    param([string]$Prefix)
    & $FixtureScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Plan $FixturePlan `
        -Mode Reset `
        -Report (Join-Path $Output "$Prefix\fixture-report.json") `
        -ValidationReport (Join-Path $Output "$Prefix\validation-report.json") `
        -VerificationOutput (Join-Path $Output "$Prefix\Reload") `
        -VerificationReport (Join-Path $Output "$Prefix\verification-report.json") | Out-Host
    if ($LASTEXITCODE -ne 0)
    {
        throw "Scalar fixture reset failed with exit code $LASTEXITCODE"
    }
}

try
{
    Write-Host "Resetting isolated scalar fixture..."
    Reset-ScalarFixture -Prefix "Fixture"
    $Fixture = [System.IO.File]::ReadAllText($FixtureReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $PackageFile = [string]$Fixture.fixtures[0].packageFilename
    Assert-UeakPath -Path $PackageFile -Description "scalar fixture package" -PathType File
    $BaselineHash = (Get-FileHash -LiteralPath $PackageFile -Algorithm SHA256).Hash
    Copy-Item -LiteralPath $PackageFile -Destination $BaselineCopy -Force

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
        throw "Snapshot refresh smoke index build failed with exit code $LASTEXITCODE"
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

    & $VenvPython $ClientScript `
        --engine-root $EngineRoot `
        --project $ProjectPath `
        --database $Database `
        --policy $Policy `
        --revision-export $RevisionExport `
        --work-root $WorkRoot `
        --backup-root $BackupRoot `
        --package-file $PackageFile `
        --error-log-first $ErrorLogFirst `
        --error-log-second $ErrorLogSecond
    if ($LASTEXITCODE -ne 0)
    {
        throw "MCP snapshot refresh smoke test failed with exit code $LASTEXITCODE"
    }
}
finally
{
    if (![string]::IsNullOrWhiteSpace($PackageFile) -and (Test-Path -LiteralPath $BaselineCopy))
    {
        Write-Host "Restoring exact scalar fixture Package bytes after snapshot refresh test..."
        Copy-Item -LiteralPath $BaselineCopy -Destination $PackageFile -Force
        $RestoredHash = (Get-FileHash -LiteralPath $PackageFile -Algorithm SHA256).Hash
        if (![string]::IsNullOrWhiteSpace($BaselineHash) -and $RestoredHash -ne $BaselineHash)
        {
            throw "Scalar fixture SHA-256 was not restored after snapshot refresh test."
        }
        Remove-Item -LiteralPath $BaselineCopy -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $WorkRoot)
    {
        Remove-Item -LiteralPath $WorkRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $BackupRoot)
    {
        Remove-Item -LiteralPath $BackupRoot -Recurse -Force
    }
}

Write-Host "MCP paired snapshot refresh smoke test passed."
