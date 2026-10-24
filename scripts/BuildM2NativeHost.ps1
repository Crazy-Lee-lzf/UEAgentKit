param(
    [string]$EngineRoot = "E:\EPICGAME\UE_5.6"
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$HostRoot = Join-Path $Root "Build\m2-native"
$HostProject = Join-Path $HostRoot "M2NativeHost.uproject"
$PluginSource = Join-Path $Root "Plugin\UEAgentKit"
$PluginLink = Join-Path $HostRoot "Plugins\UEAgentKit"
$AutoSdkRoot = Join-Path $HostRoot "AutoSDK"
$MsvcVersion = "14.44.35207"
$MsvcRoot = "D:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\$MsvcVersion"
$SdkLink = Join-Path $AutoSdkRoot "HostWin64\Win64\VS2022\$MsvcVersion"
$BuildBat = Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat"

foreach ($Required in @($BuildBat, $PluginSource, $MsvcRoot)) {
    if (!(Test-Path -LiteralPath $Required)) {
        throw "Missing required path: $Required"
    }
}

New-Item -ItemType Directory -Path $HostRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $HostRoot "Plugins") -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path $SdkLink) -Force | Out-Null

if (!(Test-Path -LiteralPath $PluginLink)) {
    New-Item -ItemType Junction -Path $PluginLink -Target $PluginSource | Out-Null
}
if (!(Test-Path -LiteralPath $SdkLink)) {
    New-Item -ItemType Junction -Path $SdkLink -Target $MsvcRoot | Out-Null
}

$ProjectJson = @'
{
  "FileVersion": 3,
  "EngineAssociation": "5.6",
  "Category": "",
  "Description": "Disposable M2 native layout broker host",
  "Plugins": [
    {
      "Name": "UEAgentKit",
      "Enabled": true
    }
  ]
}
'@
[System.IO.File]::WriteAllText($HostProject, $ProjectJson, [System.Text.UTF8Encoding]::new($false))

$PreviousSdk = $env:UE_SDKS_ROOT
$env:UE_SDKS_ROOT = $AutoSdkRoot
$LogPath = Join-Path $HostRoot "build.log"
try {
    Write-Host "Engine: $EngineRoot"
    Write-Host "Host  : $HostProject"
    Write-Host "SDK   : $AutoSdkRoot"
    & $BuildBat @(
        "UnrealEditor",
        "Win64",
        "Development",
        "-Project=$HostProject",
        "-plugin=$(Join-Path $PluginLink 'UEAgentKit.uplugin')",
        "-NoUBA",
        "-NoHotReload",
        "-WaitMutex"
    ) 2>&1 | Tee-Object -FilePath $LogPath
    if ($LASTEXITCODE -ne 0) {
        throw "UnrealBuildTool failed with exit code $LASTEXITCODE"
    }
}
finally {
    $env:UE_SDKS_ROOT = $PreviousSdk
}
Write-Host "BUILD OK"
