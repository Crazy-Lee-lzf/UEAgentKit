param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Args = ""
)

<#
.SYNOPSIS
Generates the Niagara Module Stack test fixture used by the N1 reader.

.DESCRIPTION
Runs the UEAgentKit `NiagaraStackFixture` commandlet against a host project.  The
commandlet creates:

    /Game/Dev/NS_DevStack    (system, two emitters)
    /Game/Dev/NE_DevStack_A  (emitter with the default module set)
    /Game/Dev/NE_DevStack_B  (emitter with the default module set)

Each emitter gets EmitterState + SpawnRate (EmitterUpdate), SystemLocation +
AddVelocity + a parameter module (ParticleSpawn) and UpdateAge + Color +
SolveForcesAndVelocity (ParticleUpdate), so the reader has real multi-module
stages to report.

The host project must have the UEAgentKit plugin binaries for this build.  The
dev project used by the Niagara line is:

    E:\WorkSpace\UETest\UEAKDev_Niagara_20261116\UEAKDev_Niagara_20261116.uproject

.PARAMETER ProjectPath
Host .uproject path.  Falls back to UEAK_PROJECT_PATH when omitted.

.PARAMETER Args
Extra commandlet arguments forwarded verbatim.

.EXAMPLE
pwsh -NoProfile -File scripts\GenerateNiagaraStackFixture.ps1 `
    -ProjectPath E:\WorkSpace\UETest\UEAKDev_Niagara_20261116\UEAKDev_Niagara_20261116.uproject
#>

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath

$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File
Assert-UeakPath -Path $ProjectPath -Description "Host project" -PathType File

$ProjectRoot = Split-Path -Parent $ProjectPath
$PluginDll = Join-Path $ProjectRoot "Plugins\UEAgentKit\Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll"
if (-not (Test-Path -LiteralPath $PluginDll -PathType Leaf))
{
    throw "The host project does not have a built UEAgentKit plugin DLL: $PluginDll"
}

Write-Host "Generating the Niagara stack fixture"
Write-Host "Engine  : $EngineRoot"
Write-Host "Project : $ProjectPath"

$ArgumentList = @(
    "`"$ProjectPath`"",
    "-run=NiagaraStackFixture",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NullRHI",
    "-NoSound",
    "-stdout",
    "-FullStdOutLogOutput"
)
if (-not [string]::IsNullOrWhiteSpace($Args))
{
    $ArgumentList += $Args
}

& $EditorCmd @ArgumentList
$ExitCode = $LASTEXITCODE
if ($ExitCode -ne 0)
{
    throw "NiagaraStackFixture failed with exit code $ExitCode"
}

$ContentRoot = Join-Path $ProjectRoot "Content\Dev"
foreach ($AssetName in @("NS_DevStack", "NE_DevStack_A", "NE_DevStack_B"))
{
    $AssetPath = Join-Path $ContentRoot "$AssetName.uasset"
    if (-not (Test-Path -LiteralPath $AssetPath -PathType Leaf))
    {
        throw "The fixture asset was not written: $AssetPath"
    }
    Write-Host "Created: $AssetPath"
}
