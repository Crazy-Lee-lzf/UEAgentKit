param(
    [ValidateSet("Install", "Remove")]
    [string]$Action = "Install",
    [ValidateSet("Package", "Source")]
    [string]$Mode = "Package",
    [string]$EngineRoot = "",
    [string]$PluginDirectory = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-BctToolRoot
$EngineRoot = Resolve-BctEngineRoot -EngineRoot $EngineRoot
$DefaultSourceDirectory = Join-Path $ToolRoot "Plugin\BlueprintContextTool"
$DefaultPackageDirectory = Join-Path $ToolRoot "Build\Compiled\BlueprintContextTool"
$LinkPath = Join-Path $EngineRoot "Engine\Plugins\Developer\BlueprintContextTool"

if ([string]::IsNullOrWhiteSpace($PluginDirectory))
{
    $PluginDirectory = if ($Mode -eq "Package") { $DefaultPackageDirectory } else { $DefaultSourceDirectory }
}
$PluginDirectory = [System.IO.Path]::GetFullPath($PluginDirectory)

if ($Action -eq "Install")
{
    $PluginDescriptor = Join-Path $PluginDirectory "BlueprintContextTool.uplugin"
    Assert-BctPath -Path $PluginDescriptor -Description "Plugin descriptor" -PathType File
    Ensure-BctJunction -LinkPath $LinkPath -TargetPath $PluginDirectory | Out-Null

    Write-Host "Installed engine plugin junction:"
    Write-Host "  Engine: $EngineRoot"
    Write-Host "  Link  : $LinkPath"
    Write-Host "  Target: $PluginDirectory"
    Write-Host "  Mode  : $Mode"
    exit 0
}

$Removed = Remove-BctJunction -LinkPath $LinkPath
if ($Removed)
{
    Write-Host "Removed engine plugin junction: $LinkPath"
}
else
{
    Write-Host "Engine plugin junction is not installed: $LinkPath"
}
