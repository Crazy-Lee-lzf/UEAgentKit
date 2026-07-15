param(
    [ValidateSet("Direct", "UAT")]
    [string]$Method = "Direct",
    [string]$EngineRoot = "",
    [string]$MsvcToolsRoot = "",
    [string]$PackageDirectory = "",
    [string]$TargetPlatforms = "Win64"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot

if ($Method -eq "Direct")
{
    $DirectBuildScript = Join-Path $PSScriptRoot "BuildPluginDirect.ps1"
    & $DirectBuildScript -EngineRoot $EngineRoot -MsvcToolsRoot $MsvcToolsRoot
    exit $LASTEXITCODE
}

$PluginDescriptor = Join-Path $ToolRoot "Plugin\UEAgentKit\UEAgentKit.uplugin"
$RunUAT = Join-Path $EngineRoot "Engine\Build\BatchFiles\RunUAT.bat"
if ([string]::IsNullOrWhiteSpace($PackageDirectory))
{
    $PackageDirectory = Join-Path $ToolRoot "Build\Packaged\UEAgentKit"
}
else
{
    $PackageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
}

Assert-UeakPath -Path $PluginDescriptor -Description "Plugin descriptor" -PathType File
Assert-UeakPath -Path $RunUAT -Description "RunUAT.bat" -PathType File
New-Item -ItemType Directory -Path $PackageDirectory -Force | Out-Null

Write-Warning "The UAT build path is optional and may fail on systems with Unreal Build Accelerator issues. The default Direct method is the validated build path."

& $RunUAT @(
    "BuildPlugin",
    "-Plugin=$PluginDescriptor",
    "-Package=$PackageDirectory",
    "-TargetPlatforms=$TargetPlatforms",
    "-Rocket",
    "-NoP4"
)

if ($LASTEXITCODE -ne 0)
{
    throw "BuildPlugin failed with exit code $LASTEXITCODE"
}

Write-Host "Plugin package created: $PackageDirectory"
