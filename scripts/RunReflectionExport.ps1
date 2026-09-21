param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Output = "",
    [switch]$CompactJson
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"

Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot "Output\Reflection"
}
else
{
    $Output = [System.IO.Path]::GetFullPath($Output)
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$Arguments = @(
    $ProjectPath,
    "-run=ReflectionExport",
    "-Output=$Output",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NoSound",
    "-NullRHI",
    "-stdout",
    "-FullStdOutLogOutput"
)
if ($CompactJson)
{
    $Arguments += "-CompactJson"
}

Write-Host "Running ReflectionExport..."
Write-Host "Engine  : $EngineRoot"
Write-Host "Project : $ProjectPath"
Write-Host "Output  : $Output"

& $EditorCmd @Arguments
if ($LASTEXITCODE -ne 0)
{
    throw "ReflectionExport failed with exit code $LASTEXITCODE"
}

$ReflectionFile = Join-Path $Output "reflection.json"
Assert-UeakPath -Path $ReflectionFile -Description "reflection.json" -PathType File
Write-Host "Reflection export completed: $ReflectionFile"
