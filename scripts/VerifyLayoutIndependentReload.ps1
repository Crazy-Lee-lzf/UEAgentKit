<#
.SYNOPSIS
    Produce and verify independent-process reload evidence for the experimental
    Blueprint graph-layout Save (Preview -> Apply -> Undo/Redo -> Save -> reload).

.DESCRIPTION
    READ-ONLY with respect to the project. It launches ONE fresh
    UnrealEditor-Cmd.exe BlueprintContextExport (NullRHI commandlet: no Editor
    GUI, no save, no compile, no automation test) against the already-saved
    .uasset, records that process's real PID and start time from the OS, turns
    the export into a probe document, and asks the offline harness in
    tools/layout_independent_reload_harness.py to verify the
    apply/save/probe triple against the bytes on disk.

    It refuses to run while an Unreal Editor is open for this project, and it
    never saves, recompiles, or writes the package. If the harness cannot prove
    that a different, later process read the same bytes and the same positions,
    it fails closed and independentReloadVerified stays false.

    Prerequisite: ue_apply_blueprint_graph_layout_experimental and
    ue_save_blueprint_graph_layout_experimental already ran, and their two tool
    responses were saved as JSON. The Editor that performed the Save must be
    closed; pass its PID and start/termination timestamps.

.EXAMPLE
    .\scripts\VerifyLayoutIndependentReload.ps1 `
        -Asset /Game/UEAgentKitP11/BP_LayoutP11 `
        -ApplyEvidence Output\p11\apply.json `
        -SaveEvidence Output\p11\save.json `
        -EditorPid 12345 `
        -EditorStartedUtc 2026-10-20T02:00:00.000Z `
        -EditorTerminatedUtc 2026-10-20T02:10:00.000Z
#>
param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [Parameter(Mandatory = $true)][string]$Asset,
    [Parameter(Mandatory = $true)][string]$ApplyEvidence,
    [Parameter(Mandatory = $true)][string]$SaveEvidence,
    [Parameter(Mandatory = $true)][int]$EditorPid,
    [Parameter(Mandatory = $true)][string]$EditorStartedUtc,
    [Parameter(Mandatory = $true)][string]$EditorTerminatedUtc,
    [string]$Output = "",
    [string]$Graph = "",
    [switch]$SkipEditorProcessCheck
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File
Assert-UeakPath -Path $ApplyEvidence -Description "Apply evidence JSON" -PathType File
Assert-UeakPath -Path $SaveEvidence -Description "Save evidence JSON" -PathType File

$Harness = Join-Path $ToolRoot "tools\layout_independent_reload_harness.py"
Assert-UeakPath -Path $Harness -Description "reload harness" -PathType File

$Python = Get-Command "py" -ErrorAction SilentlyContinue
if ($null -eq $Python)
{
    throw "py launcher not found; Python 3.12 is required for the reload harness."
}

if (!$SkipEditorProcessCheck)
{
    $Running = @(Get-Process -Name "UnrealEditor", "UnrealEditor-Cmd" -ErrorAction SilentlyContinue)
    if ($Running.Count -gt 0)
    {
        throw ("An Unreal Editor process is still running (PID " +
            (($Running | ForEach-Object { $_.Id }) -join ", ") +
            "). Close the saving Editor first; independent reload evidence must come from a later process.")
    }
}

$AssetFile = $null
$ContentRoot = Join-Path (Split-Path -Parent $ProjectPath) "Content"
$AssetLeaf = ($Asset -split "/")[-1]
$PackagePath = $Asset -replace "^/", "" -replace "\.", "/"
$PackagePath = ($PackagePath -split "/")[0..(($PackagePath -split "/").Length - 2)] -join "/"
$AssetFile = Join-Path (Join-Path $ContentRoot $PackagePath) ($AssetLeaf + ".uasset")
Assert-UeakPath -Path $AssetFile -Description "saved .uasset" -PathType File

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $Output = Join-Path $ToolRoot ("Output\independent-reload\" + $Stamp)
}
$Output = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$Arguments = @(
    $ProjectPath,
    "-run=BlueprintContextExport",
    "-Output=$Output",
    "-Profile=logic",
    "-Format=json",
    "-Asset=$Asset",
    "-IncludeLayout",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NoSound",
    "-NullRHI",
    "-stdout",
    "-FullStdOutLogOutput"
)

$ArgumentLine = ($Arguments | ForEach-Object {
    if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
}) -join ' '

Write-Host "Probe process : $EditorCmd"
Write-Host "Probe output  : $Output"
Write-Host "Asset         : $Asset"

# Start-Process -PassThru gives the real OS identity of the probe process, so the
# evidence does not have to trust anything the export claims about itself.
$ProbeProcess = Start-Process -FilePath $EditorCmd -ArgumentList $ArgumentLine -PassThru -Wait -NoNewWindow
$ProbePid = $ProbeProcess.Id
$ProbeStartedUtc = $ProbeProcess.StartTime.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
$ProbeFinishedUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")

if ($ProbeProcess.ExitCode -ne 0)
{
    throw "BlueprintContextExport probe failed with exit code $($ProbeProcess.ExitCode)."
}

$CanonicalRoot = Join-Path $Output "canonical"
$ExportJson = Get-ChildItem -Path $CanonicalRoot -Filter "*.json" -Recurse -File |
    Where-Object { $_.Name -like ("*" + $AssetLeaf + "*.json") } |
    Select-Object -First 1
if ($null -eq $ExportJson)
{
    throw "The probe produced no canonical JSON under $CanonicalRoot."
}

$ProbePath = Join-Path $Output "reload-probe.json"
$ProbeArguments = @(
    "-3.12", $Harness, "build-probe",
    "--export", $ExportJson.FullName,
    "--asset", $AssetFile,
    "--pid", $ProbePid,
    "--started-utc", $ProbeStartedUtc,
    "--reloaded-utc", $ProbeFinishedUtc,
    "--command", ("`"" + $EditorCmd + "`" " + $ArgumentLine),
    "--executable", $EditorCmd,
    "--host", $env:COMPUTERNAME,
    "--out", $ProbePath
)
if (![string]::IsNullOrWhiteSpace($Graph))
{
    $ProbeArguments += @("--graph", $Graph)
}
& $Python.Source @ProbeArguments
if ($LASTEXITCODE -ne 0)
{
    throw "build-probe failed with exit code $LASTEXITCODE."
}

$EvidencePath = Join-Path $Output "independent-reload-evidence.json"
$VerifyArguments = @(
    "-3.12", $Harness, "verify",
    "--apply", (Resolve-Path -LiteralPath $ApplyEvidence).Path,
    "--save", (Resolve-Path -LiteralPath $SaveEvidence).Path,
    "--probe", $ProbePath,
    "--asset", $AssetFile,
    "--editor-pid", $EditorPid,
    "--editor-started-utc", $EditorStartedUtc,
    "--editor-terminated-utc", $EditorTerminatedUtc,
    "--out", $EvidencePath
)
& $Python.Source @VerifyArguments
$VerifyExit = $LASTEXITCODE

Write-Host ""
Write-Host "Probe PID     : $ProbePid (started $ProbeStartedUtc)"
Write-Host "Editor PID    : $EditorPid (started $EditorStartedUtc, terminated $EditorTerminatedUtc)"
Write-Host "Evidence      : $EvidencePath"

if ($VerifyExit -eq 0)
{
    Write-Host "independentReloadVerified = true (independent-process evidence complete)"
}
else
{
    Write-Host "independentReloadVerified = false (fail closed; see $EvidencePath)"
}

exit $VerifyExit
