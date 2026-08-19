param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = ""
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
$ExportScript = Join-Path $PSScriptRoot "RunExport.ps1"
$ClientScript = Join-Path $ToolRoot "tests\integration\mcp_blueprint_semantic_diff_smoke.py"
$FixturePlan = Join-Path $ToolRoot "tests\fixtures\multi_operation_transaction_plan.json"
foreach ($Required in @($VenvPython, $FixtureScript, $ExportScript, $ClientScript, $FixturePlan))
{
    Assert-UeakPath -Path $Required -Description ([IO.Path]::GetFileName($Required)) -PathType File
}

$Output = Join-Path $ToolRoot "Output\McpBlueprintSemanticDiffSmoke"
$BackupRoot = Join-Path $ToolRoot "Backups\McpBlueprintSemanticDiffSmoke"
foreach ($Target in @($Output, $BackupRoot))
{
    if (Test-Path -LiteralPath $Target)
    {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
}

$FixtureDirectory = Join-Path $Output "Fixture"
$FixtureReport = Join-Path $FixtureDirectory "fixture-report.json"
$RevisionExport = Join-Path $Output "Revision"
$Database = Join-Path $Output "Index\ueak.sqlite3"
$Policy = Join-Path $Output "policy.json"
$WorkRoot = Join-Path $Output "Workflow"
$ErrorLog = Join-Path $Output "Logs\mcp-stderr.log"
$Summary = Join-Path $Output "semantic-diff-summary.json"
$BlueprintPackage = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint"
$BlueprintPath = "$BlueprintPackage.BP_TransactionBlueprint"
$BlueprintClass = "/Script/Engine.Blueprint"
$TransactionDirectory = Join-Path $ProjectDirectory "Content\UEAgentKitWriteTests\Transactions"
$TransactionDirectoryExisted = Test-Path -LiteralPath $TransactionDirectory
$Succeeded = $false

function Write-Utf8Json([string]$Path, [object]$Value)
{
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    [IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 20) + "`r`n"),
        [Text.UTF8Encoding]::new($false))
}

try
{
    if (@(Get-Process UnrealEditor,UnrealEditor-Cmd -ErrorAction SilentlyContinue).Count -gt 0)
    {
        throw "Blueprint Semantic Diff Smoke requires Unreal Editor processes to be closed."
    }
    & $FixtureScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Plan $FixturePlan `
        -Mode Reset `
        -Report $FixtureReport `
        -ValidationReport (Join-Path $FixtureDirectory "validation-report.json") `
        -VerificationOutput (Join-Path $FixtureDirectory "Reload") `
        -VerificationReport (Join-Path $FixtureDirectory "verification-report.json")
    if ($LASTEXITCODE -ne 0) { throw "Blueprint fixture reset failed: $LASTEXITCODE" }

    & $ExportScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Asset $BlueprintPackage `
        -Output $RevisionExport `
        -Profile full `
        -Format json `
        -IncludeUnchangedDefaults
    if ($LASTEXITCODE -ne 0) { throw "Blueprint Revision Export failed: $LASTEXITCODE" }

    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Database)) -Force | Out-Null
    & $VenvPython (Join-Path $PSScriptRoot "ue-agent.py") index build $RevisionExport --database $Database --force --project-key $ProjectName | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Blueprint Smoke index build failed: $LASTEXITCODE" }

    Write-Utf8Json $Policy ([ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitWriteTests/Transactions")
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @("setVariableDefault")
        allowedAssetClasses = @($BlueprintClass)
        allowedAssetProperties = @()
        allowedMaterialParameters = @()
        allowedDataTableFields = @()
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    })

    $Fixture = [IO.File]::ReadAllText($FixtureReport, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $BlueprintFixture = @($Fixture.fixtures) |
        Where-Object { [string]$_.id -eq "transaction-blueprint" } |
        Select-Object -First 1
    if ($null -eq $BlueprintFixture) { throw "Blueprint fixture report entry is missing." }
    $PackageFile = [string]$BlueprintFixture.packageFilename
    Assert-UeakPath -Path $PackageFile -Description "Blueprint transaction fixture" -PathType File

    & $VenvPython $ClientScript `
        --engine-root $EngineRoot `
        --project $ProjectPath `
        --database $Database `
        --policy $Policy `
        --revision-export $RevisionExport `
        --work-root $WorkRoot `
        --backup-root $BackupRoot `
        --package-file $PackageFile `
        --error-log $ErrorLog `
        --summary-report $Summary
    if ($LASTEXITCODE -ne 0) { throw "Blueprint Semantic Diff MCP Smoke failed: $LASTEXITCODE" }
    $Succeeded = $true
}
finally
{
    if (!$Succeeded -and (Test-Path -LiteralPath $FixtureReport))
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
        if ($LASTEXITCODE -ne 0)
        {
            Write-Warning "Blueprint fixture recovery failed with exit code $LASTEXITCODE"
        }
    }
    if (!$TransactionDirectoryExisted -and (Test-Path -LiteralPath $TransactionDirectory))
    {
        $ContentRoot = [IO.Path]::GetFullPath((Join-Path $ProjectDirectory "Content"))
        $ResolvedTransactionDirectory = [IO.Path]::GetFullPath($TransactionDirectory)
        $ContentPrefix = $ContentRoot.TrimEnd([char]'\', [char]'/') + [IO.Path]::DirectorySeparatorChar
        if (!$ResolvedTransactionDirectory.StartsWith($ContentPrefix, [StringComparison]::OrdinalIgnoreCase))
        {
            throw "Refusing to remove generated fixture directory outside project Content: $ResolvedTransactionDirectory"
        }
        Remove-Item -LiteralPath $ResolvedTransactionDirectory -Recurse -Force
    }
}

if (!$Succeeded) { throw "Blueprint Semantic Diff Smoke did not complete successfully." }
Write-Host "Blueprint Semantic Diff MCP Smoke passed for $BlueprintPath."
