param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [switch]$SkipBuild,
    [switch]$SkipFixture,
    [string]$Filter = "UEAgentKit.Niagara.ModuleStackReaderContract",
    [int]$TimeoutSeconds = 900
)

<#
.SYNOPSIS
Installs the built UEAgentKit plugin into the Niagara dev project, regenerates the
stack fixture and runs the native Niagara module stack contract test.

.DESCRIPTION
Steps:
  1. copy Build\Compiled\UEAgentKit\Binaries\Win64 into <project>\Plugins\UEAgentKit
  2. run the NiagaraStackFixture commandlet (creates /Game/Dev/NS_DevStack)
  3. run UnrealEditor-Cmd with -ExecCmds "Automation RunTests <Filter>"
  4. report the automation report directory and exit code

The dev project used by the Niagara line is:
  E:\WorkSpace\UETest\UEAKDev_Niagara_20261116\UEAKDev_Niagara_20261116.uproject
#>

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath

$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File

$ProjectRoot = Split-Path -Parent $ProjectPath
$ProjectPluginRoot = Join-Path $ProjectRoot "Plugins\UEAgentKit"

if (-not $SkipBuild)
{
    & (Join-Path $PSScriptRoot "BuildPluginDirect.ps1")
    if ($LASTEXITCODE -ne 0)
    {
        throw "BuildPluginDirect.ps1 failed with exit code $LASTEXITCODE"
    }
}

# --- 1. install the freshly built binaries into the host project -------------
$CompiledBinaries = Join-Path $ToolRoot "Build\Compiled\UEAgentKit\Binaries\Win64"
Assert-UeakPath -Path $CompiledBinaries -Description "Compiled plugin binaries" -PathType Directory

$TargetBinaries = Join-Path $ProjectPluginRoot "Binaries\Win64"
New-Item -ItemType Directory -Force -Path $TargetBinaries | Out-Null
foreach ($FileName in @("UnrealEditor-UEAgentKitEditor.dll", "UnrealEditor-UEAgentKitEditor.pdb", "UnrealEditor.modules"))
{
    $Source = Join-Path $CompiledBinaries $FileName
    if (Test-Path -LiteralPath $Source -PathType Leaf)
    {
        Copy-Item -LiteralPath $Source -Destination (Join-Path $TargetBinaries $FileName) -Force
    }
}
Write-Host "Installed plugin binaries into $TargetBinaries"

# --- 2. regenerate the fixture ---------------------------------------------
if (-not $SkipFixture)
{
    & (Join-Path $PSScriptRoot "GenerateNiagaraStackFixture.ps1") -EngineRoot $EngineRoot -ProjectPath $ProjectPath
    if ($LASTEXITCODE -ne 0)
    {
        throw "Fixture generation failed with exit code $LASTEXITCODE"
    }
}

# --- 3. run the native contract test ---------------------------------------
$EvidenceDir = Join-Path $ToolRoot "Output\NiagaraN1"
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReportDir = Join-Path $EvidenceDir "AutomationReport-$Stamp"
$LogPath = Join-Path $EvidenceDir "automation-$Stamp.log"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

$ArgumentLine = @(
    "`"$ProjectPath`"",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NullRHI",
    "-NoSound",
    "-ExecCmds=`"Automation RunTests $Filter`"",
    "-TestExit=`"Automation Test Queue Empty`"",
    "-ReportExportPath=`"$ReportDir`"",
    "-log",
    "-stdout",
    "-FullStdOutLogOutput"
) -join " "

Write-Host "Running automation: $Filter"
$Process = Start-Process -FilePath $EditorCmd -ArgumentList $ArgumentLine -WindowStyle Minimized -PassThru `
    -RedirectStandardOutput $LogPath -RedirectStandardError "$LogPath.err"
$Finished = $Process.WaitForExit($TimeoutSeconds * 1000)
if (-not $Finished)
{
    $Process.Kill()
    throw "Automation run exceeded $TimeoutSeconds seconds and was terminated"
}

Write-Host "UnrealEditor-Cmd exit code: $($Process.ExitCode)"
Write-Host "Log   : $LogPath"
Write-Host "Report: $ReportDir"

if ($Process.ExitCode -ne 0)
{
    throw "Automation run failed with exit code $($Process.ExitCode) (see $LogPath)"
}
