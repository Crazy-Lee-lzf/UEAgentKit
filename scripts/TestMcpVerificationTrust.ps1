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
$ProjectName = [IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$ProjectDirectory = Split-Path -Parent $ProjectPath
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$FixtureScript = Join-Path $PSScriptRoot "RunWriteFixturePlan.ps1"
$CatalogScript = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$ExportScript = Join-Path $PSScriptRoot "RunExport.ps1"
$AgentScript = Join-Path $PSScriptRoot "ue-agent.py"
$ClientScript = Join-Path $ToolRoot "tests\integration\mcp_verification_trust_smoke.py"
$S1FixturePlan = Join-Path $ToolRoot "tests\fixtures\closed_loop_live_write_plan.json"
$S2FixturePlan = Join-Path $ToolRoot "tests\fixtures\multi_operation_transaction_plan.json"
$UnrealEditor = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
$CompiledPlugin = Join-Path $ToolRoot "Build\Compiled\UEAgentKit\Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll"
foreach ($Required in @(
    $VenvPython,
    $FixtureScript,
    $CatalogScript,
    $ExportScript,
    $AgentScript,
    $ClientScript,
    $S1FixturePlan,
    $S2FixturePlan,
    $UnrealEditor,
    $CompiledPlugin
))
{
    Assert-UeakPath -Path $Required -Description ([IO.Path]::GetFileName($Required)) -PathType File
}

$Output = Join-Path $ToolRoot "Output\McpVerificationTrustSmoke"
$BackupRoot = Join-Path $ToolRoot "Backups\McpVerificationTrustSmoke"
foreach ($Target in @($Output, $BackupRoot))
{
    $ResolvedTarget = [IO.Path]::GetFullPath($Target)
    $ResolvedToolRoot = [IO.Path]::GetFullPath($ToolRoot).TrimEnd([char]'\', [char]'/') + [IO.Path]::DirectorySeparatorChar
    if (!$ResolvedTarget.StartsWith($ResolvedToolRoot, [StringComparison]::OrdinalIgnoreCase))
    {
        throw "Refusing to prepare Smoke path outside the workspace: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget)
    {
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ResolvedTarget -Force | Out-Null
}

$S1Root = Join-Path $Output "S1"
$S2Root = Join-Path $Output "S2"
$S1FixtureDirectory = Join-Path $S1Root "Fixture"
$S2FixtureDirectory = Join-Path $S2Root "Fixture"
$S1FixtureReport = Join-Path $S1FixtureDirectory "fixture-report.json"
$S2FixtureReport = Join-Path $S2FixtureDirectory "fixture-report.json"
$S1RevisionExport = Join-Path $S1Root "Revision"
$S2RevisionExport = Join-Path $S2Root "Revision"
$S1Database = Join-Path $S1Root "Index\ueak.sqlite3"
$S2Database = Join-Path $S2Root "Index\ueak.sqlite3"
$S1Policy = Join-Path $S1Root "policy.json"
$S2Policy = Join-Path $S2Root "policy.json"
$S1WorkRoot = Join-Path $S1Root "Workflow"
$S2WorkRoot = Join-Path $S2Root "Workflow"
$S1BackupRoot = Join-Path $BackupRoot "S1"
$S2BackupRoot = Join-Path $BackupRoot "S2"
$S1ErrorLog = Join-Path $S1Root "Logs\mcp-stderr.log"
$S2ErrorLog = Join-Path $S2Root "Logs\mcp-stderr.log"
$SessionMarker = Join-Path $Output "session-initialized.marker"
$Summary = Join-Path $Output "verification-trust-summary.json"
$EditorStdout = Join-Path $Output "Logs\Editor-stdout.log"
$EditorStderr = Join-Path $Output "Logs\Editor-stderr.log"
$DescriptorPath = Join-Path $ProjectDirectory "Saved\UEAgentKit\EditorBridge.json"
$S1AssetRoot = "/Game/UEAgentKitWriteTests/ClosedLoop"
$S2BlueprintPackage = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint"
$S1ScalarClass = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
$S2BlueprintClass = "/Script/Engine.Blueprint"
$TransactionDirectory = Join-Path $ProjectDirectory "Content\UEAgentKitWriteTests\Transactions"
$TransactionDirectoryExisted = Test-Path -LiteralPath $TransactionDirectory
$EditorProcess = $null
$ClientSucceeded = $false
$S1RecoverySucceeded = $false
$S2RecoverySucceeded = $false
$S2PackageFile = ""
$S2PackageHashBefore = ""

function Write-Utf8Json
{
    param([string]$Path, [object]$Value)
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    [IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 30) + "`r`n"),
        [Text.UTF8Encoding]::new($false))
}

function Read-BridgeDescriptor
{
    if (!(Test-Path -LiteralPath $DescriptorPath))
    {
        return $null
    }
    try
    {
        return [IO.File]::ReadAllText($DescriptorPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    }
    catch
    {
        return $null
    }
}

function Reset-Fixture
{
    param(
        [string]$Plan,
        [string]$Root,
        [string]$Report
    )
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    & $FixtureScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Plan $Plan `
        -Mode Reset `
        -Report $Report `
        -ValidationReport (Join-Path $Root "validation-report.json") `
        -VerificationOutput (Join-Path $Root "Reload") `
        -VerificationReport (Join-Path $Root "verification-report.json") | Out-Host
    return $LASTEXITCODE -eq 0
}

try
{
    $ExistingDescriptor = Read-BridgeDescriptor
    if ($null -ne $ExistingDescriptor)
    {
        $ExistingProcess = Get-Process -Id ([int]$ExistingDescriptor.processId) -ErrorAction SilentlyContinue
        if ($null -ne $ExistingProcess)
        {
            throw "R3 Verification Trust Smoke requires the existing Live Editor to be closed."
        }
        Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue
    }
    if (@(Get-Process UnrealEditor,UnrealEditor-Cmd -ErrorAction SilentlyContinue).Count -gt 0)
    {
        throw "R3 Verification Trust Smoke requires all Unreal Editor processes to be closed."
    }

    Write-Host "Resetting isolated R3 S1 and S2 fixtures..."
    if (!(Reset-Fixture -Plan $S1FixturePlan -Root $S1FixtureDirectory -Report $S1FixtureReport))
    {
        throw "R3 S1 fixture reset failed."
    }
    if (!(Reset-Fixture -Plan $S2FixturePlan -Root $S2FixtureDirectory -Report $S2FixtureReport))
    {
        throw "R3 S2 fixture reset failed."
    }

    & $CatalogScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Root $S1AssetRoot `
        -Output $S1RevisionExport
    if ($LASTEXITCODE -ne 0) { throw "R3 S1 Revision Export failed: $LASTEXITCODE" }

    & $ExportScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Asset $S2BlueprintPackage `
        -Output $S2RevisionExport `
        -Profile full `
        -Format json `
        -IncludeUnchangedDefaults
    if ($LASTEXITCODE -ne 0) { throw "R3 S2 Blueprint Revision Export failed: $LASTEXITCODE" }

    foreach ($Database in @($S1Database, $S2Database))
    {
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Database)) -Force | Out-Null
    }
    & $VenvPython $AgentScript index build $S1RevisionExport --database $S1Database --force --project-key $ProjectName | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "R3 S1 index build failed: $LASTEXITCODE" }
    & $VenvPython $AgentScript index build $S2RevisionExport --database $S2Database --force --project-key $ProjectName | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "R3 S2 index build failed: $LASTEXITCODE" }

    Write-Utf8Json -Path $S1Policy -Value ([ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @($S1AssetRoot)
        allowedReferenceRoots = @($S1AssetRoot)
        allowedReferenceClasses = @()
        allowedOperations = @("setAssetProperty")
        allowedAssetClasses = @($S1ScalarClass)
        allowedAssetProperties = @("$S1ScalarClass#IntValue")
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    })
    Write-Utf8Json -Path $S2Policy -Value ([ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests/Transactions")
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @("setVariableDefault")
        allowedAssetClasses = @($S2BlueprintClass)
        allowedAssetProperties = @()
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    })

    $S2Fixture = [IO.File]::ReadAllText($S2FixtureReport, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $S2BlueprintFixture = @($S2Fixture.fixtures) |
        Where-Object { [string]$_.id -eq "transaction-blueprint" } |
        Select-Object -First 1
    if ($null -eq $S2BlueprintFixture) { throw "R3 S2 Blueprint fixture report entry is missing." }
    $S2PackageFile = [string]$S2BlueprintFixture.packageFilename
    Assert-UeakPath -Path $S2PackageFile -Description "R3 S2 Blueprint fixture" -PathType File
    $S2PackageHashBefore = (Get-FileHash -LiteralPath $S2PackageFile -Algorithm SHA256).Hash.ToLowerInvariant()

    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($EditorStdout)) -Force | Out-Null
    $EditorProcess = Start-Process `
        -FilePath $UnrealEditor `
        -ArgumentList @(
            $ProjectPath,
            "-unattended",
            "-nosplash",
            "-NoSound",
            "-NoP4",
            "-stdout",
            "-FullStdOutLogOutput") `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $EditorStdout `
        -RedirectStandardError $EditorStderr

    $Deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $Ready = $false
    while ((Get-Date) -lt $Deadline)
    {
        if ($EditorProcess.HasExited)
        {
            throw "Unreal Editor exited before publishing the R3 Live Editor Bridge descriptor."
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
    if (!$Ready) { throw "Timed out waiting for the R3 Live Editor Bridge descriptor." }

    Remove-Item -LiteralPath $SessionMarker -Force -ErrorAction SilentlyContinue
    & $VenvPython $ClientScript `
        --engine-root $EngineRoot `
        --project $ProjectPath `
        --s1-database $S1Database `
        --s1-policy $S1Policy `
        --s1-revision-export $S1RevisionExport `
        --s1-work-root $S1WorkRoot `
        --s1-backup-root $S1BackupRoot `
        --s1-error-log $S1ErrorLog `
        --s2-database $S2Database `
        --s2-policy $S2Policy `
        --s2-revision-export $S2RevisionExport `
        --s2-work-root $S2WorkRoot `
        --s2-backup-root $S2BackupRoot `
        --s2-error-log $S2ErrorLog `
        --s2-package-file $S2PackageFile `
        --session-marker $SessionMarker `
        --summary-report $Summary
    if ($LASTEXITCODE -ne 0)
    {
        throw "R3 Verification Trust MCP client failed with exit code $LASTEXITCODE"
    }
    $ClientSucceeded = $true
}
finally
{
    if ($null -ne $EditorProcess)
    {
        Stop-Process -Id $EditorProcess.Id -Force -ErrorAction SilentlyContinue
        $ExitDeadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $ExitDeadline)
        {
            if ($null -eq (Get-Process -Id $EditorProcess.Id -ErrorAction SilentlyContinue))
            {
                break
            }
            Start-Sleep -Milliseconds 250
        }
    }
    $Descriptor = Read-BridgeDescriptor
    if ($null -ne $Descriptor -and $null -ne $EditorProcess -and [int]$Descriptor.processId -eq $EditorProcess.Id)
    {
        Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue
    }

    if (Test-Path -LiteralPath $S1FixtureReport)
    {
        $S1RecoverySucceeded = Reset-Fixture `
            -Plan $S1FixturePlan `
            -Root (Join-Path $Output "Recovery\S1") `
            -Report (Join-Path $Output "Recovery\S1\fixture-report.json")
    }
    if (Test-Path -LiteralPath $S2FixtureReport)
    {
        $S2RecoverySucceeded = Reset-Fixture `
            -Plan $S2FixturePlan `
            -Root (Join-Path $Output "Recovery\S2") `
            -Report (Join-Path $Output "Recovery\S2\fixture-report.json")
    }

    if ($ClientSucceeded -and (Test-Path -LiteralPath $Summary))
    {
        $SummaryValue = [IO.File]::ReadAllText($Summary, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $S2HashRestored = $false
        if (![string]::IsNullOrWhiteSpace($S2PackageFile) -and (Test-Path -LiteralPath $S2PackageFile))
        {
            $S2HashAfter = (Get-FileHash -LiteralPath $S2PackageFile -Algorithm SHA256).Hash.ToLowerInvariant()
            $S2HashRestored = $S2HashAfter -eq $S2PackageHashBefore
        }
        $SummaryValue.recovery.finalFixtureResetPending = $false
        Add-Member -InputObject $SummaryValue.recovery -NotePropertyName s1FixtureResetPassed -NotePropertyValue $S1RecoverySucceeded -Force
        Add-Member -InputObject $SummaryValue.recovery -NotePropertyName s2FixtureResetPassed -NotePropertyValue $S2RecoverySucceeded -Force
        Add-Member -InputObject $SummaryValue.recovery -NotePropertyName s2PackageHashRestored -NotePropertyValue $S2HashRestored -Force
        Add-Member -InputObject $SummaryValue.recovery -NotePropertyName editorStopped -NotePropertyValue ($null -eq (Get-Process -Id $EditorProcess.Id -ErrorAction SilentlyContinue)) -Force
        Write-Utf8Json -Path $Summary -Value $SummaryValue
    }

    if (!$TransactionDirectoryExisted -and (Test-Path -LiteralPath $TransactionDirectory))
    {
        $ContentRoot = [IO.Path]::GetFullPath((Join-Path $ProjectDirectory "Content"))
        $ResolvedTransactions = [IO.Path]::GetFullPath($TransactionDirectory)
        $ContentPrefix = $ContentRoot.TrimEnd([char]'\', [char]'/') + [IO.Path]::DirectorySeparatorChar
        if (!$ResolvedTransactions.StartsWith($ContentPrefix, [StringComparison]::OrdinalIgnoreCase))
        {
            throw "Refusing to remove generated R3 fixture directory outside Content: $ResolvedTransactions"
        }
        Remove-Item -LiteralPath $ResolvedTransactions -Recurse -Force
    }
}

if (!$ClientSucceeded) { throw "R3 Verification Trust Smoke did not complete successfully." }
if (!$S1RecoverySucceeded -or !$S2RecoverySucceeded)
{
    throw "R3 Verification Trust Smoke completed, but fixture recovery did not pass."
}
Write-Host "R3 Verification Trust S1-S5 Smoke passed."
Write-Host "Summary: $Summary"
