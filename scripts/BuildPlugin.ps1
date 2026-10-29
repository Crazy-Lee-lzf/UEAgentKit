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
Assert-UeakNoReparsePath -Path $MsvcToolchain.FullName
Assert-UeakPlainTree -Path (Join-Path $ToolRoot "Plugin\UEAgentKit")
$BuildRoot = Join-Path $ToolRoot "Build"
$OutputRoot = Join-Path $ToolRoot "Output"
$PackageAllowed = $false
foreach ($AllowedRoot in @($BuildRoot, $OutputRoot))
{
    $FullAllowed = [System.IO.Path]::GetFullPath($AllowedRoot).TrimEnd([char]'\', [char]'/')
    $Prefix = $FullAllowed + [System.IO.Path]::DirectorySeparatorChar
    if ($PackageDirectory.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase))
    {
        Assert-UeakUnderDirectory -Path $PackageDirectory -AllowedRoot $AllowedRoot
        $PackageAllowed = $true
        break
    }
}
if (!$PackageAllowed)
{
    throw "Plugin package directory must stay within Build or Output."
}
New-Item -ItemType Directory -Path $PackageDirectory -Force | Out-Null

$PreviousSdkRoot = [Environment]::GetEnvironmentVariable("UE_SDKS_ROOT", "Process")
$PreviousAllowUbaExecutor = $env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor
# Use Visual Studio's installed toolchain directly; do not use an SDK mirror.
[Environment]::SetEnvironmentVariable("UE_SDKS_ROOT", $null, "Process")
$env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor = "false"
try
{
    Write-Host "UAT MSVC     : $($MsvcToolchain.FullName)"
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
    [Environment]::SetEnvironmentVariable("UE_SDKS_ROOT", $PreviousSdkRoot, "Process")
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
