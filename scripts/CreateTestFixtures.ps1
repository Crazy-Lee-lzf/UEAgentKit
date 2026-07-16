param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$ObjectTarget = "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter.BP_ThirdPersonCharacter",
    [string]$ClassTargetBlueprint = "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
$FixtureScript = Join-Path $ToolRoot "tests\fixtures\create_ue_semantic_fixtures.py"

Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File
Assert-UeakPath -Path $FixtureScript -Description "UE semantic fixture script" -PathType File

$PreviousObjectTarget = $env:UEAK_FIXTURE_OBJECT_TARGET
$PreviousClassTarget = $env:UEAK_FIXTURE_CLASS_TARGET_BLUEPRINT
try
{
    $env:UEAK_FIXTURE_OBJECT_TARGET = $ObjectTarget
    $env:UEAK_FIXTURE_CLASS_TARGET_BLUEPRINT = $ClassTargetBlueprint

    $Arguments = @(
        $ProjectPath,
        "-run=pythonscript",
        "-script=$FixtureScript",
        "-EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities",
        "-unattended",
        "-nop4",
        "-nosplash",
        "-NoSound",
        "-NullRHI",
        "-stdout",
        "-FullStdOutLogOutput"
    )

    Write-Host "Creating UE Agent Kit semantic fixtures..."
    Write-Host "Engine  : $EngineRoot"
    Write-Host "Project : $ProjectPath"
    Write-Host "Script  : $FixtureScript"

    & $EditorCmd @Arguments
    if ($LASTEXITCODE -ne 0)
    {
        throw "Fixture creation failed with exit code $LASTEXITCODE"
    }

    $ProjectDirectory = Split-Path -Parent $ProjectPath
    $ResultPath = Join-Path $ProjectDirectory "Saved\UEAgentKitFixtures\semantic_fixtures.json"
    Write-Host "Fixture creation completed."
    Write-Host "Result  : $ResultPath"
}
finally
{
    $env:UEAK_FIXTURE_OBJECT_TARGET = $PreviousObjectTarget
    $env:UEAK_FIXTURE_CLASS_TARGET_BLUEPRINT = $PreviousClassTarget
}
