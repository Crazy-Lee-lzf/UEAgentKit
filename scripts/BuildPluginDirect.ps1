param(
    [string]$EngineRoot = "",
    [string]$MsvcToolsRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$MsvcToolchain = Resolve-UeakMsvcToolchain -MsvcToolsRoot $MsvcToolsRoot

$SourcePlugin = Join-Path $ToolRoot "Plugin\UEAgentKit"
$PluginDescriptor = Join-Path $SourcePlugin "UEAgentKit.uplugin"
$HostRoot = Join-Path $ToolRoot "Build\DirectHost"
$HostProject = Join-Path $HostRoot "HostProject.uproject"
$HostPluginParent = Join-Path $HostRoot "Plugins"
$HostPlugin = Join-Path $HostPluginParent "UEAgentKit"
$HostPluginDescriptor = Join-Path $HostPlugin "UEAgentKit.uplugin"
$BuildBat = Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat"
$AutoSdkRoot = Join-Path $ToolRoot "AutoSDK"
$AutoSdkToolchain = Join-Path $AutoSdkRoot "HostWin64\Win64\VS2022\$($MsvcToolchain.Name)"
$CompiledRoot = Join-Path $ToolRoot "Build\Compiled"
$CompiledPlugin = Join-Path $CompiledRoot "UEAgentKit"

Assert-UeakPath -Path $BuildBat -Description "Unreal Build.bat" -PathType File
Assert-UeakPath -Path $PluginDescriptor -Description "Plugin descriptor" -PathType File

Ensure-UeakJunction -LinkPath $AutoSdkToolchain -TargetPath $MsvcToolchain.FullName -ReplaceDifferentJunction | Out-Null
New-Item -ItemType Directory -Path $HostPluginParent -Force | Out-Null
Ensure-UeakJunction -LinkPath $HostPlugin -TargetPath $SourcePlugin -ReplaceDifferentJunction | Out-Null

$HostProjectContent = '{ "FileVersion": 3, "Plugins": [ { "Name": "UEAgentKit", "Enabled": true } ] }'
[System.IO.File]::WriteAllText($HostProject, $HostProjectContent, [System.Text.UTF8Encoding]::new($false))

$PreviousAutoSdkRoot = $env:UE_SDKS_ROOT
$env:UE_SDKS_ROOT = $AutoSdkRoot

try
{
    Write-Host "Engine       : $EngineRoot"
    Write-Host "MSVC         : $($MsvcToolchain.FullName)"
    Write-Host "Plugin       : $SourcePlugin"
    Write-Host "Host project : $HostProject"
    Write-Host "UBA          : disabled"

    & $BuildBat @(
        "UnrealEditor",
        "Win64",
        "Development",
        "-Project=$HostProject",
        "-plugin=$HostPluginDescriptor",
        "-NoUBA",
        "-NoHotReload",
        "-WaitMutex"
    )

    if ($LASTEXITCODE -ne 0)
    {
        throw "UnrealBuildTool failed with exit code $LASTEXITCODE"
    }

    if (Test-Path -LiteralPath $CompiledPlugin)
    {
        Remove-Item -LiteralPath $CompiledPlugin -Recurse -Force
    }

    New-Item -ItemType Directory -Path $CompiledRoot -Force | Out-Null
    Copy-Item -LiteralPath $SourcePlugin -Destination $CompiledRoot -Recurse -Force

    foreach ($Directory in @(
        (Join-Path $CompiledPlugin "Intermediate"),
        (Join-Path $CompiledPlugin "Saved")
    ))
    {
        if (Test-Path -LiteralPath $Directory)
        {
            Remove-Item -LiteralPath $Directory -Recurse -Force
        }
    }

    $BuiltDll = Join-Path $CompiledPlugin "Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll"
    Assert-UeakPath -Path $BuiltDll -Description "Compiled plugin DLL" -PathType File

    Write-Host "BUILD SUCCEEDED"
    Write-Host "Compiled plugin: $CompiledPlugin"
    Write-Host "DLL            : $BuiltDll"
}
finally
{
    $env:UE_SDKS_ROOT = $PreviousAutoSdkRoot
}
