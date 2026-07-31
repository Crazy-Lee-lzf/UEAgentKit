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
$FixtureScript = Join-Path $PSScriptRoot "RunWriteFixturePlan.ps1"
$CatalogScript = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$FixturePlan = Join-Path $ToolRoot "tests\fixtures\scalar_patch_regression_plan.json"
$ClientScript = Join-Path $ToolRoot "tests\integration\mcp_live_write_smoke.py"
$UnrealEditor = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
$CompiledPlugin = Join-Path $ToolRoot "Build\Compiled\UEAgentKit\Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll"
foreach ($Required in @($VenvPython, $FixtureScript, $CatalogScript, $FixturePlan, $ClientScript, $UnrealEditor, $CompiledPlugin))
{
    Assert-UeakPath -Path $Required -Description ([System.IO.Path]::GetFileName($Required)) -PathType File
}

$Output = Join-Path $ToolRoot "Output\McpLiveWriteSmoke"
if (Test-Path -LiteralPath $Output)
{
    Remove-Item -LiteralPath $Output -Recurse -Force
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$BackupRoot = Join-Path $ToolRoot "Backups\McpLiveWriteSmoke"
if (Test-Path -LiteralPath $BackupRoot)
{
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
$EditorStdout = Join-Path $Output "Logs\Editor-stdout.log"
$EditorStderr = Join-Path $Output "Logs\Editor-stderr.log"
$DescriptorPath = Join-Path $ProjectDirectory "Saved\UEAgentKit\EditorBridge.json"
$AssetPackage = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget"
$AssetClass = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
$EditorProcess = $null
$PackageFile = ""
$PackageHashBefore = ""
$Succeeded = $false

function Write-Utf8Json
{
    param([string]$Path, [object]$Value)
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 20) + "`r`n"),
        [System.Text.UTF8Encoding]::new($false))
}

function Read-BridgeDescriptor
{
    if (!(Test-Path -LiteralPath $DescriptorPath))
    {
        return $null
    }
    try
    {
        return [System.IO.File]::ReadAllText(
            $DescriptorPath,
            [System.Text.UTF8Encoding]::new($false)) | ConvertFrom-Json
    }
    catch
    {
        return $null
    }
}

try
{
    $ExistingDescriptor = Read-BridgeDescriptor
    if ($null -ne $ExistingDescriptor)
    {
        $ExistingProcess = Get-Process -Id ([int]$ExistingDescriptor.processId) -ErrorAction SilentlyContinue
        if ($null -ne $ExistingProcess)
        {
            throw "A Live Editor Bridge is already active for this test project. Close that Editor first."
        }
        Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue
    }

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
        throw "Live Write smoke index build failed with exit code $LASTEXITCODE"
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
    $PackageHashBefore = (Get-FileHash -LiteralPath $PackageFile -Algorithm SHA256).Hash.ToLowerInvariant()

    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($EditorStdout)) -Force | Out-Null
    $EditorProcess = Start-Process `
        -FilePath $UnrealEditor `
        -ArgumentList @(
            $ProjectPath,
            "-unattended",
            "-nosplash",
            "-NoSound",
            "-NoP4",
            "-NullRHI",
            "-stdout",
            "-FullStdOutLogOutput") `
        -PassThru `
        -RedirectStandardOutput $EditorStdout `
        -RedirectStandardError $EditorStderr

    $Deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $Ready = $false
    while ((Get-Date) -lt $Deadline)
    {
        if ($EditorProcess.HasExited)
        {
            throw "Unreal Editor exited before publishing the Live Editor Bridge descriptor."
        }
        $Descriptor = Read-BridgeDescriptor
        if ($null -ne $Descriptor -and
            [int]$Descriptor.processId -eq $EditorProcess.Id -and
            [int]$Descriptor.port -gt 0)
        {
            $Ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
        $EditorProcess.Refresh()
    }
    if (!$Ready)
    {
        throw "Timed out waiting for the Live Editor Bridge descriptor."
    }

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
        throw "MCP Live Editor Write smoke test failed with exit code $LASTEXITCODE"
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

    if (![string]::IsNullOrWhiteSpace($PackageFile) -and (Test-Path -LiteralPath $PackageFile))
    {
        $PackageHashAfter = (Get-FileHash -LiteralPath $PackageFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($PackageHashAfter -ne $PackageHashBefore)
        {
            $Succeeded = $false
            Write-Warning "Live Write changed the fixture package on disk; resetting the fixture."
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
    }
}

if (!$Succeeded)
{
    throw "MCP Live Editor Write smoke test did not complete successfully."
}
Write-Host "MCP Live Editor Write smoke test passed."
