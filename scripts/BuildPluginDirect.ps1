param(
    [string]$EngineRoot = "",
    [string]$MsvcToolsRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$CompiledRoot = Join-Path $ToolRoot "Build\\Compiled"
$CompiledPlugin = Join-Path $CompiledRoot "UEAgentKit"
$PackageScript = Join-Path $PSScriptRoot "BuildPlugin.ps1"

# Fail before removing any existing package when the compiler is unavailable.
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$MsvcToolchain = Resolve-UeakMsvcToolchain -MsvcToolsRoot $MsvcToolsRoot
Assert-UeakNoReparsePath -Path $MsvcToolchain.FullName

# The direct-entry compatibility path uses the standard UAT package workflow.
# There is no linked host plugin or mapped compiler installation.
Remove-UeakSafeDirectory -Path $CompiledPlugin -AllowedRoot $CompiledRoot
& $PackageScript -Method UAT -EngineRoot $EngineRoot -MsvcToolsRoot $MsvcToolchain.FullName -PackageDirectory $CompiledPlugin
if ($LASTEXITCODE -ne 0)
{
    throw "UE plugin build failed with exit code $LASTEXITCODE"
}
$BuiltDll = Join-Path $CompiledPlugin "Binaries\\Win64\\UnrealEditor-UEAgentKitEditor.dll"
Assert-UeakPath -Path $BuiltDll -Description "Compiled plugin DLL" -PathType File
Write-Host "BUILD SUCCEEDED"
Write-Host "Compiled plugin: $CompiledPlugin"
Write-Host "DLL            : $BuiltDll"
