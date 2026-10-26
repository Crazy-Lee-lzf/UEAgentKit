<#
.SYNOPSIS
    Run the real-Editor P4 bounded Blueprint node-removal acceptance and verify a fresh-process reload.

.DESCRIPTION
    Uses the disposable Build/DirectHost fixture project.  The Editor automation
    test creates one ordinary event graph with nine nodes and four exact edges,
    proves the default-OFF gate, unknown-parameter, invalid-identifier,
    missing-node, class-mismatch, comment-group/knot, too-many-connections,
    removed-edge, stale-edge-set, stale-plan, dirty-package, non-focused-asset,
    wrong-confirmation and Save-before-Apply rejections, then performs
    Preview -> Apply -> Undo/Redo -> Save for one isolated node and for one node
    with exactly one enumerated edge, and writes evidence.json.

    A second, brand-new UnrealEditor-Cmd process runs the read-only
    BlueprintNodeRemoveReload commandlet, which loads the saved package from
    disk and verifies that both deleted node GUIDs are absent, every remaining
    node GUID/class and pin name/GUID/direction/category is unchanged, the exact
    final edge set is present, the removed edge is absent and the untouched
    fan-out/execution edges survive, plus the asset SHA-256.

    The fixture project must not opt in to ExperimentalGraphPatchEdit; the
    default-off gate proof depends on the opt-in being absent.

.EXAMPLE
    .\scripts\VerifyBlueprintNodeRemoveIndependentReload.ps1 -EngineRoot E:\EPICGAME\UE_5.6
#>
param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot

if ([string]::IsNullOrWhiteSpace($ProjectPath))
{
    $ProjectPath = Join-Path $ToolRoot "Build\DirectHost\HostProject.uproject"
}
$ProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)

$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
$PluginDll = Join-Path $ToolRoot "Plugin\UEAgentKit\Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll"
Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File
Assert-UeakPath -Path $ProjectPath -Description "disposable fixture project" -PathType File
Assert-UeakPath -Path $PluginDll -Description "compiled UEAgentKit editor DLL" -PathType File

$ProjectRoot = Split-Path -Parent $ProjectPath
$DefaultEngineIni = Join-Path $ProjectRoot "Config\DefaultEngine.ini"
$FixtureOptsInIni = $false
if (Test-Path -LiteralPath $DefaultEngineIni)
{
    $FixtureIniText = Get-Content -LiteralPath $DefaultEngineIni -Raw
    $FixtureOptsInIni = $FixtureIniText -match "ExperimentalGraphPatchEdit\s*=\s*True"
}
if ($FixtureOptsInIni)
{
    throw ("The fixture project opts in to ExperimentalGraphPatchEdit; the default-off gate " +
        "proof requires the opt-in to stay unset.")
}

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot ("Output\blueprint-node-remove-acceptance\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$Output = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$SavedDir = Join-Path $ProjectRoot "Saved\BlueprintNodeRemove"
$EvidencePath = Join-Path $SavedDir "evidence.json"
$ReloadPath = Join-Path $SavedDir "reload.json"
$PieGatePath = Join-Path $SavedDir "pie-gate.json"
foreach ($StalePath in @($EvidencePath, $ReloadPath, $PieGatePath))
{
    if (Test-Path -LiteralPath $StalePath)
    {
        Remove-Item -LiteralPath $StalePath -Force
    }
}

$ReportDir = Join-Path $Output "report"
$AutomationLog = Join-Path $Output "editor-automation.log"
Write-Host "Fixture project : $ProjectPath"
Write-Host "Automation log  : $AutomationLog"

$AutomationArguments = @(
    $ProjectPath,
    "-ExecCmds=Automation RunTests UEAgentKit.NodeRemove",
    "-TestExit=Automation Test Queue Empty",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NullRHI",
    "-NoSound",
    "-stdout",
    "-FullStdOutLogOutput",
    "-ReportExportPath=$ReportDir"
)
& $EditorCmd @AutomationArguments *>&1 | Tee-Object -FilePath $AutomationLog | Out-Null

$ReportIndex = Join-Path $ReportDir "index.json"
if (!(Test-Path -LiteralPath $ReportIndex))
{
    throw "The Editor automation run produced no report at $ReportIndex."
}
$Report = Get-Content -LiteralPath $ReportIndex -Raw | ConvertFrom-Json
$Tests = @($Report.tests)
$NotPassed = @($Tests | Where-Object { $_.state -ne "Success" })
if ([int]$Report.failed -ne 0 -or $NotPassed.Count -gt 0)
{
    throw ("Editor automation did not pass: failed=$($Report.failed) " +
        "tests=$((($Tests | ForEach-Object { $_.state + ':' + $_.fullTestPath }) -join ', '))")
}
Assert-UeakPath -Path $EvidencePath -Description "Editor node-removal acceptance evidence" -PathType File
$Evidence = Get-Content -LiteralPath $EvidencePath -Raw | ConvertFrom-Json

if (!(Test-Path -LiteralPath $PieGatePath))
{
    throw "The PIE gate evidence was not written at $PieGatePath."
}
$PieGate = Get-Content -LiteralPath $PieGatePath -Raw | ConvertFrom-Json
if (-not [bool]$PieGate.success)
{
    throw ("The PIE gate evidence did not succeed: pieStarted=$($PieGate.pieStarted) " +
        "preview=$($PieGate.previewRejected)/$($PieGate.previewErrorCode) " +
        "apply=$($PieGate.applyRejected)/$($PieGate.applyErrorCode)")
}

$ReloadLog = Join-Path $Output "editor-reload.log"
$ReloadArguments = @(
    $ProjectPath,
    "-run=BlueprintNodeRemoveReload",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NullRHI",
    "-NoSound",
    "-stdout",
    "-FullStdOutLogOutput"
)
& $EditorCmd @ReloadArguments *>&1 | Tee-Object -FilePath $ReloadLog | Out-Null
$ReloadExitCode = $LASTEXITCODE

if (!(Test-Path -LiteralPath $ReloadPath))
{
    throw "The reload commandlet produced no result at $ReloadPath (log: $ReloadLog)."
}
$Reload = Get-Content -LiteralPath $ReloadPath -Raw | ConvertFrom-Json

$ExpectedNodeCount = [int]$Evidence.expectedNodeCount
$ExpectedEdgeCount = [int]$Evidence.expectedEdgeCount
$DeletedNodes = @($Evidence.deletedNodeGuids)
$ExpectedRemoved = @($Evidence.removedEdges).Count
$ExpectedUntouched = @($Evidence.untouchedEdges).Count
$DifferentProcess = [int64]$Evidence.processId -ne [int64]$Reload.processId
$AcceptanceChecks = @(
    ($ReloadExitCode -eq 0),
    [bool]$Reload.verified,
    [bool]$Reload.assetSha256Verified,
    ([int]$Reload.expectedNodeCount -eq $ExpectedNodeCount),
    ([int]$Reload.actualNodeCount -eq $ExpectedNodeCount),
    ([int]$Reload.expectedEdgeCount -eq $ExpectedEdgeCount),
    ([int]$Reload.actualEdgeCount -eq $ExpectedEdgeCount),
    ([int]$Reload.deletedNodeCount -eq $DeletedNodes.Count),
    ([int]$Reload.removedEdgeCount -eq $ExpectedRemoved),
    ([int]$Reload.untouchedEdgeCount -eq $ExpectedUntouched),
    (@($Reload.mismatches).Count -eq 0),
    $DifferentProcess
)
$Accepted = -not ($AcceptanceChecks -contains $false)

$Summary = [ordered]@{
    schemaVersion = "ueak.blueprint-node-remove-acceptance/1.0"
    projectPath = $ProjectPath
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    automation = [ordered]@{
        log = $AutomationLog
        report = $ReportIndex
        succeeded = [int]$Report.succeeded
        failed = [int]$Report.failed
        tests = @($Tests | ForEach-Object {
            [ordered]@{ path = $_.fullTestPath; state = $_.state }
        })
    }
    editorEvidence = [ordered]@{
        path = $EvidencePath
        processId = $Evidence.processId
        assetPath = $Evidence.assetPath
        graphGuid = $Evidence.graphGuid
        assetSha256 = $Evidence.assetSha256
        isolatedPlanId = $Evidence.isolatedPlanId
        connectedPlanId = $Evidence.connectedPlanId
        edgeSetHashBefore = $Evidence.edgeSetHashBefore
        edgeSetHashAfterIsolated = $Evidence.edgeSetHashAfterIsolated
        edgeSetHashAfterConnected = $Evidence.edgeSetHashAfterConnected
        deletedNodeGuids = $DeletedNodes
        checks = $Evidence.checks
    }
    pieGate = [ordered]@{
        path = $PieGatePath
        previewErrorCode = $PieGate.previewErrorCode
        applyErrorCode = $PieGate.applyErrorCode
        edgesUnchanged = $PieGate.edgesUnchanged
    }
    independentReload = [ordered]@{
        log = $ReloadLog
        path = $ReloadPath
        exitCode = [int]$ReloadExitCode
        processId = $Reload.processId
        verified = $Reload.verified
        assetSha256Verified = $Reload.assetSha256Verified
        nodes = "$($Reload.actualNodeCount)/$($Reload.expectedNodeCount)"
        edges = "$($Reload.actualEdgeCount)/$($Reload.expectedEdgeCount)"
        deleted = $Reload.deletedNodeCount
        removed = $Reload.removedEdgeCount
        untouched = $Reload.untouchedEdgeCount
        mismatches = @($Reload.mismatches)
    }
    differentProcess = [bool]$DifferentProcess
    accepted = [bool]$Accepted
}
$SummaryPath = Join-Path $Output "acceptance-summary.json"
$Summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $SummaryPath -Encoding utf8

Write-Host "Editor PID      : $($Evidence.processId)"
Write-Host "Reload PID      : $($Reload.processId)"
Write-Host "Reload verified : $($Reload.verified)"
Write-Host "Nodes           : $($Reload.actualNodeCount)/$($Reload.expectedNodeCount)"
Write-Host "Edges           : $($Reload.actualEdgeCount)/$($Reload.expectedEdgeCount)"
Write-Host "Deleted/removed : $($Reload.deletedNodeCount)/$($Reload.removedEdgeCount)"
Write-Host "Untouched       : $($Reload.untouchedEdgeCount)"
Write-Host "Mismatches      : $(@($Reload.mismatches).Count)"
Write-Host "Summary         : $SummaryPath"

if ($Accepted)
{
    Write-Host "independentReloadVerified = true (fresh-process evidence complete)"
    exit 0
}

Write-Host "independentReloadVerified = false (fail closed; see $ReloadPath)"
exit 1
