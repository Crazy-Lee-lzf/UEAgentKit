<#
.SYNOPSIS
    Run the real-Editor P3 Blueprint single-edge rewire acceptance and verify a fresh-process reload.

.DESCRIPTION
    Uses the disposable Build/DirectHost fixture project.  The Editor automation
    test creates one ordinary event graph with five exact edges, proves the
    default-OFF gate, unknown-parameter, invalid-operation, missing
    node/pin/GUID, pin-name/direction/kind/type, unsafe schema break, stale edge
    set, stale plan, dirty package, non-focused asset, wrong confirmation and
    Save-before-Apply rejections, then performs Preview -> Apply -> Undo/Redo ->
    Save for one rewire and one explicit disconnect and writes evidence.json.

    A second, brand-new UnrealEditor-Cmd process runs the read-only
    BlueprintRewireReload commandlet, which loads the saved package from disk
    and verifies the persisted asset SHA-256, every node GUID/class, every pin
    name/GUID/direction/category, the exact final edge set, the absence of the
    two removed edges and the presence of the added and untouched edges.

    The fixture project must not opt in to ExperimentalGraphPatchEdit; the
    default-off gate proof depends on the opt-in being absent.

.EXAMPLE
    .\scripts\VerifyBlueprintRewireIndependentReload.ps1 -EngineRoot E:\EPICGAME\UE_5.6
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
    $Output = Join-Path $ToolRoot ("Output\blueprint-rewire-acceptance\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$Output = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$SavedDir = Join-Path $ProjectRoot "Saved\BlueprintRewire"
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
    "-ExecCmds=Automation RunTests UEAgentKit.Rewire",
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
Assert-UeakPath -Path $EvidencePath -Description "Editor rewire acceptance evidence" -PathType File
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
    "-run=BlueprintRewireReload",
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
$ExpectedPins = @($Evidence.pins)
$ExpectedRemoved = @($Evidence.removedEdges).Count
$ExpectedAdded = @($Evidence.addedEdges).Count
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
    ([int]$Reload.removedEdgeCount -eq $ExpectedRemoved),
    ([int]$Reload.addedEdgeCount -eq $ExpectedAdded),
    ([int]$Reload.untouchedEdgeCount -eq $ExpectedUntouched),
    (@($Reload.mismatches).Count -eq 0),
    ($ExpectedPins.Count -eq $ExpectedNodeCount),
    $DifferentProcess
)
$Accepted = -not ($AcceptanceChecks -contains $false)

$Summary = [ordered]@{
    schemaVersion = "ueak.blueprint-rewire-acceptance/1.0"
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
        rewirePlanId = $Evidence.rewirePlanId
        disconnectPlanId = $Evidence.disconnectPlanId
        edgeSetHashBefore = $Evidence.edgeSetHashBefore
        edgeSetHashAfterRewire = $Evidence.edgeSetHashAfterRewire
        edgeSetHashAfterDisconnect = $Evidence.edgeSetHashAfterDisconnect
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
        removed = $Reload.removedEdgeCount
        added = $Reload.addedEdgeCount
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
Write-Host "Removed/added   : $($Reload.removedEdgeCount)/$($Reload.addedEdgeCount)"
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
