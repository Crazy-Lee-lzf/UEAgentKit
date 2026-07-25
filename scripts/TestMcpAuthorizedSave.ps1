param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [ValidateRange(30, 300)]
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$ProjectName = [System.IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$ProjectDirectory = Split-Path -Parent $ProjectPath
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$UnrealEditor = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
$FixtureScript = Join-Path $PSScriptRoot "RunWriteFixturePlan.ps1"
$FixturePlan = Join-Path $ToolRoot "tests\fixtures\scalar_patch_regression_plan.json"
$CatalogScript = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$TestScript = Join-Path $ToolRoot "tests\integration\mcp_authorized_save_smoke.py"
$OutputRoot = Join-Path $ToolRoot "Output\McpAuthorizedSaveSmoke"
$BackupRoot = Join-Path $ToolRoot "Backups\McpAuthorizedSaveSmoke"
$DescriptorPath = Join-Path $ProjectDirectory "Saved\UEAgentKit\EditorBridge.json"
$EditorStdout = Join-Path $OutputRoot "Logs\Editor-stdout.log"
$EditorStderr = Join-Path $OutputRoot "Logs\Editor-stderr.log"
$McpStderr = Join-Path $OutputRoot "Logs\mcp-stderr.log"
$FixtureDirectory = Join-Path $OutputRoot "Fixture"
$FixtureReport = Join-Path $FixtureDirectory "fixture-report.json"
$RevisionExport = Join-Path $OutputRoot "Revision"
$Database = Join-Path $OutputRoot "Index\ueak.sqlite3"
$Policy = Join-Path $OutputRoot "policy.json"
$WorkRoot = Join-Path $OutputRoot "Workflow"
$AssetPackage = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget"
$AssetClass = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"

foreach ($Required in @($VenvPython, $UnrealEditor, $FixtureScript, $FixturePlan, $CatalogScript, $TestScript))
{
    Assert-UeakPath -Path $Required -Description ([System.IO.Path]::GetFileName($Required)) -PathType File
}

function Remove-SafeTree
{
    param([string]$Path, [string]$Boundary)
    if (!(Test-Path -LiteralPath $Path)) { return }
    $Full = [System.IO.Path]::GetFullPath($Path)
    $Root = [System.IO.Path]::GetFullPath($Boundary).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (!$Full.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase))
    {
        throw "Refusing to remove path outside fixed boundary: $Full"
    }
    $Reparse = Get-ChildItem -LiteralPath $Full -Force -Recurse -ErrorAction Stop |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 } |
        Select-Object -First 1
    if ($null -ne $Reparse) { throw "Path contains a Junction or symbolic link: $($Reparse.FullName)" }
    Remove-Item -LiteralPath $Full -Recurse -Force
}

function Write-Utf8Json
{
    param([string]$Path, [object]$Value)
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    $Json = $Value | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($Path, $Json + "`r`n", [System.Text.UTF8Encoding]::new($false))
}

function Read-BridgeDescriptor
{
    if (!(Test-Path -LiteralPath $DescriptorPath)) { return $null }
    try
    {
        return ([System.IO.File]::ReadAllText($DescriptorPath, [System.Text.UTF8Encoding]::new($false)) | ConvertFrom-Json)
    }
    catch { return $null }
}

Remove-SafeTree -Path $OutputRoot -Boundary (Join-Path $ToolRoot "Output")
Remove-SafeTree -Path $BackupRoot -Boundary (Join-Path $ToolRoot "Backups")
New-Item -ItemType Directory -Path (Split-Path -Parent $EditorStdout) -Force | Out-Null
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null

$EditorProcess = $null
$PackageFile = ""
$Succeeded = $false
try
{
    & $FixtureScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Plan $FixturePlan `
        -Mode Reset `
        -Report $FixtureReport `
        -ValidationReport (Join-Path $FixtureDirectory "validation-report.json") `
        -VerificationOutput (Join-Path $FixtureDirectory "Reload") `
        -VerificationReport (Join-Path $FixtureDirectory "verification-report.json")
    if ($LASTEXITCODE -ne 0) { throw "Scalar fixture reset failed with exit code $LASTEXITCODE" }

    $Fixture = [System.IO.File]::ReadAllText($FixtureReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $PackageFile = [string]$Fixture.fixtures[0].packageFilename
    Assert-UeakPath -Path $PackageFile -Description "scalar fixture package" -PathType File

    & $CatalogScript -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Asset $AssetPackage -Output $RevisionExport
    if ($LASTEXITCODE -ne 0) { throw "Scalar Revision Export failed with exit code $LASTEXITCODE" }

    New-Item -ItemType Directory -Path (Split-Path -Parent $Database) -Force | Out-Null
    & $VenvPython (Join-Path $PSScriptRoot "ue-agent.py") index build $RevisionExport --database $Database --force --project-key $ProjectName | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Authorized-save index build failed with exit code $LASTEXITCODE" }

    Write-Utf8Json -Path $Policy -Value ([ordered]@{
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
    })

    $Existing = Read-BridgeDescriptor
    if ($null -ne $Existing -and $null -ne (Get-Process -Id ([int]$Existing.processId) -ErrorAction SilentlyContinue))
    {
        throw "A Live Editor Bridge is already active for this project. Close that Editor before this destructive regression."
    }
    Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue

    $EditorProcess = Start-Process `
        -FilePath $UnrealEditor `
        -ArgumentList @(
            $ProjectPath,
            "-UEAgentKitEnableTestHooks",
            "-unattended",
            "-nosplash",
            "-NoSound",
            "-NoP4",
            "-stdout",
            "-FullStdOutLogOutput"
        ) `
        -PassThru `
        -RedirectStandardOutput $EditorStdout `
        -RedirectStandardError $EditorStderr

    $Deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $Ready = $false
    while ((Get-Date) -lt $Deadline)
    {
        if ($EditorProcess.HasExited) { throw "Unreal Editor exited before publishing the Bridge descriptor." }
        $Descriptor = Read-BridgeDescriptor
        if ($null -ne $Descriptor -and [int]$Descriptor.processId -eq $EditorProcess.Id -and [int]$Descriptor.port -gt 0)
        {
            $Ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
        $EditorProcess.Refresh()
    }
    if (!$Ready) { throw "Timed out waiting for the Live Editor Bridge descriptor." }

    & $VenvPython $TestScript `
        --engine-root $EngineRoot `
        --project $ProjectPath `
        --database $Database `
        --policy $Policy `
        --revision-export $RevisionExport `
        --work-root $WorkRoot `
        --backup-root $BackupRoot `
        --package-file $PackageFile `
        --error-log $McpStderr
    if ($LASTEXITCODE -ne 0) { throw "Authorized-save MCP smoke failed with exit code $LASTEXITCODE" }

    if (Select-String -LiteralPath $EditorStdout -Pattern "Fatal error:|Assertion failed:|EXCEPTION_ACCESS_VIOLATION" -Quiet)
    {
        throw "The main Editor log contains a crash marker."
    }
    $Succeeded = $true
}
finally
{
    if ($null -ne $EditorProcess -and !$EditorProcess.HasExited)
    {
        Stop-Process -Id $EditorProcess.Id -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $EditorProcess.Id -Timeout 15 -ErrorAction SilentlyContinue
    }
    $Descriptor = Read-BridgeDescriptor
    if ($null -ne $Descriptor -and $null -ne $EditorProcess -and [int]$Descriptor.processId -eq $EditorProcess.Id)
    {
        Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue
    }

    if (![string]::IsNullOrWhiteSpace($PackageFile))
    {
        & $FixtureScript `
            -EngineRoot $EngineRoot `
            -ProjectPath $ProjectPath `
            -Plan $FixturePlan `
            -Mode Reset `
            -Report (Join-Path $OutputRoot "Recovery\fixture-report.json") `
            -ValidationReport (Join-Path $OutputRoot "Recovery\validation-report.json") `
            -VerificationOutput (Join-Path $OutputRoot "Recovery\Reload") `
            -VerificationReport (Join-Path $OutputRoot "Recovery\verification-report.json") | Out-Host
        if ($LASTEXITCODE -ne 0)
        {
            throw "Authorized-save fixture recovery failed with exit code $LASTEXITCODE"
        }
        $RecoveryVerification = [System.IO.File]::ReadAllText(
            (Join-Path $OutputRoot "Recovery\verification-report.json"),
            [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        if ($RecoveryVerification.verified -ne $true -or [int]$RecoveryVerification.verifiedCount -ne 1)
        {
            throw "Authorized-save recovery did not independently verify the scalar fixture baseline."
        }
    }
}

if (!$Succeeded) { throw "Authorized-save regression did not complete." }
Write-Host "Authorized-save MCP smoke test passed and fixture baseline was restored."
