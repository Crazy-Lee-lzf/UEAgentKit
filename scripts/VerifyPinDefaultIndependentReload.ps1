<#
.SYNOPSIS
    Run the real-Editor P1 pin-default acceptance and verify a fresh-process reload.

.DESCRIPTION
    Uses the disposable Build/DirectHost fixture project.  The Editor automation
    test creates a four-type fixture (bool/int/double/string input pins), proves
    the default-OFF gate, dirty-package, connected-pin, invalid-input,
    stale-plan, confirmation and save-before-apply rejections, then performs
    Preview -> Apply -> Undo/Redo -> Save and writes evidence.json.

    A second, brand-new UnrealEditor-Cmd process runs the read-only
    PinDefaultExpansionReload commandlet, which loads the saved package from disk
    and verifies the four persisted defaults, pin GUIDs, exact node set, exact
    link set and asset SHA-256.  The script accepts only when that later process
    proves the bytes on disk.

    The fixture project must not opt in to ExperimentalGraphPatchEdit; the
    default-off gate proof depends on the opt-in being absent.

.EXAMPLE
    .\scripts\VerifyPinDefaultIndependentReload.ps1 -EngineRoot E:\EPICGAME\UE_5.6
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
    $Output = Join-Path $ToolRoot ("Output\pin-default-acceptance\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$Output = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$SavedDir = Join-Path $ProjectRoot "Saved\PinDefaultExpansion"
$EvidencePath = Join-Path $SavedDir "evidence.json"
$ReloadPath = Join-Path $SavedDir "reload.json"
if (Test-Path -LiteralPath $ReloadPath)
{
    Remove-Item -LiteralPath $ReloadPath -Force
}

$ReportDir = Join-Path $Output "report"
$AutomationLog = Join-Path $Output "editor-automation.log"
Write-Host "Fixture project : $ProjectPath"
Write-Host "Automation log  : $AutomationLog"

$AutomationArguments = @(
    $ProjectPath,
    "-ExecCmds=Automation RunTests UEAgentKit.PinDefault",
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
Assert-UeakPath -Path $EvidencePath -Description "Editor acceptance evidence" -PathType File
$Evidence = Get-Content -LiteralPath $EvidencePath -Raw | ConvertFrom-Json

$ReloadLog = Join-Path $Output "editor-reload.log"
$ReloadArguments = @(
    $ProjectPath,
    "-run=PinDefaultExpansionReload",
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

$ExpectedNodes = @($Evidence.expectedNodeGuids).Count
$ExpectedLinks = @($Evidence.expectedLinks).Count
$DifferentProcess = [int64]$Evidence.processId -ne [int64]$Reload.processId
$AcceptanceChecks = @(
    ($ReloadExitCode -eq 0),
    [bool]$Reload.verified,
    [bool]$Reload.assetSha256Verified,
    ([int]$Reload.expectedDefaultCount -eq 4),
    ([int]$Reload.actualDefaultCount -eq 4),
    (@($Reload.mismatches).Count -eq 0),
    ([int]$Reload.nodeCount -eq $ExpectedNodes),
    ([int]$Reload.linkCount -eq $ExpectedLinks),
    $DifferentProcess
)
$Accepted = -not ($AcceptanceChecks -contains $false)

$Summary = [ordered]@{
    schemaVersion = "ueak.pin-default-acceptance/1.0"
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
        planId = $Evidence.planId
        assetSha256 = $Evidence.assetSha256
        undoVerified = $Evidence.undoVerified
        redoVerified = $Evidence.redoVerified
        checks = $Evidence.checks
    }
    independentReload = [ordered]@{
        log = $ReloadLog
        path = $ReloadPath
        exitCode = [int]$ReloadExitCode
        processId = $Reload.processId
        verified = $Reload.verified
        assetSha256Verified = $Reload.assetSha256Verified
        defaults = "$($Reload.actualDefaultCount)/$($Reload.expectedDefaultCount)"
        nodeCount = $Reload.nodeCount
        linkCount = $Reload.linkCount
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
Write-Host "Defaults        : $($Reload.actualDefaultCount)/$($Reload.expectedDefaultCount)"
Write-Host "Nodes / links   : $($Reload.nodeCount) / $($Reload.linkCount)"
Write-Host "Mismatches      : $(@($Reload.mismatches).Count)"
Write-Host "Summary         : $SummaryPath"

if ($Accepted)
{
    Write-Host "independentReloadVerified = true (fresh-process evidence complete)"
    exit 0
}

Write-Host "independentReloadVerified = false (fail closed; see $ReloadPath)"
exit 1
