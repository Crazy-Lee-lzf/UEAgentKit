param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$MsvcToolsRoot = "",
    [switch]$RequireVenv
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$ResolvedEngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$MsvcToolchain = Resolve-UeakMsvcToolchain -MsvcToolsRoot $MsvcToolsRoot
$BasePython = Resolve-UeakPythonExecutable
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$PluginDescriptor = Join-Path $ToolRoot "Plugin\UEAgentKit\UEAgentKit.uplugin"

$ResolvedProjectPath = $null
if (![string]::IsNullOrWhiteSpace($ProjectPath) -or ![string]::IsNullOrWhiteSpace($env:UEAK_PROJECT_PATH))
{
    $ResolvedProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
}
else
{
    $ProjectsInCurrentDirectory = @(Get-ChildItem -LiteralPath (Get-Location).Path -Filter *.uproject -File -ErrorAction SilentlyContinue)
    if ($ProjectsInCurrentDirectory.Count -eq 1)
    {
        $ResolvedProjectPath = $ProjectsInCurrentDirectory[0].FullName
    }
}

$Checks = @(
    [PSCustomObject]@{ Name = "Tool root"; Exists = (Test-Path -LiteralPath $ToolRoot -PathType Container); Path = $ToolRoot },
    [PSCustomObject]@{ Name = "Plugin descriptor"; Exists = (Test-Path -LiteralPath $PluginDescriptor -PathType Leaf); Path = $PluginDescriptor },
    [PSCustomObject]@{ Name = "UE Build.bat"; Exists = (Test-Path -LiteralPath (Join-Path $ResolvedEngineRoot "Engine\Build\BatchFiles\Build.bat") -PathType Leaf); Path = (Join-Path $ResolvedEngineRoot "Engine\Build\BatchFiles\Build.bat") },
    [PSCustomObject]@{ Name = "UnrealEditor-Cmd.exe"; Exists = (Test-Path -LiteralPath (Join-Path $ResolvedEngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe") -PathType Leaf); Path = (Join-Path $ResolvedEngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe") },
    [PSCustomObject]@{ Name = "MSVC cl.exe"; Exists = (Test-Path -LiteralPath (Join-Path $MsvcToolchain.FullName "bin\Hostx64\x64\cl.exe") -PathType Leaf); Path = (Join-Path $MsvcToolchain.FullName "bin\Hostx64\x64\cl.exe") },
    [PSCustomObject]@{ Name = "Base Python"; Exists = (Test-UeakPythonVersion -PythonExecutable $BasePython); Path = $BasePython }
)

if ($ResolvedProjectPath)
{
    $Checks += [PSCustomObject]@{ Name = "Unreal project"; Exists = (Test-Path -LiteralPath $ResolvedProjectPath -PathType Leaf); Path = $ResolvedProjectPath }
}

if ($RequireVenv -or (Test-Path -LiteralPath $VenvPython -PathType Leaf))
{
    $Checks += [PSCustomObject]@{ Name = "Project Python"; Exists = (Test-UeakPythonVersion -PythonExecutable $VenvPython); Path = $VenvPython }
}

Write-Host "=== Resolved environment ==="
Write-Host "Tool root : $ToolRoot"
Write-Host "Engine    : $ResolvedEngineRoot"
Write-Host "MSVC      : $($MsvcToolchain.FullName)"
Write-Host "Python    : $BasePython"
Write-Host "Project   : $(if ($ResolvedProjectPath) { $ResolvedProjectPath } else { '<not configured>' })"
Write-Host ""

$Checks | Format-Table -AutoSize

$MissingChecks = @($Checks | Where-Object { !$_.Exists })
if ($MissingChecks.Count -gt 0)
{
    throw "$($MissingChecks.Count) required environment check(s) failed."
}

Write-Host "ENVIRONMENT VALIDATION PASSED"
