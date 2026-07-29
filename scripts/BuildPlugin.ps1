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
$MsvcToolchain = Resolve-UeakMsvcToolchain -MsvcToolsRoot $MsvcToolsRoot
$AutoSdkRoot = Join-Path $ToolRoot "AutoSDK"
$AutoSdkToolchain = Join-Path $AutoSdkRoot "HostWin64\Win64\VS2022\$($MsvcToolchain.Name)"
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
Ensure-UeakJunction -LinkPath $AutoSdkToolchain -TargetPath $MsvcToolchain.FullName -ReplaceDifferentJunction | Out-Null

$PreviousAutoSdkRoot = $env:UE_SDKS_ROOT
$PreviousAllowUbaExecutor = $env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor
$env:UE_SDKS_ROOT = $AutoSdkRoot
$env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor = "false"
try
{
    Write-Host "UAT MSVC     : $($MsvcToolchain.FullName)"
    Write-Host "UAT AutoSDK  : $AutoSdkRoot"
    Write-Host "UAT UBA      : disabled"
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
}
finally
{
    $env:UE_SDKS_ROOT = $PreviousAutoSdkRoot
    if ($null -eq $PreviousAllowUbaExecutor)
    {
        Remove-Item Env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor -ErrorAction SilentlyContinue
    }
    else
    {
        $env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor = $PreviousAllowUbaExecutor
    }
}

Write-Host "Plugin package created: $PackageDirectory"
