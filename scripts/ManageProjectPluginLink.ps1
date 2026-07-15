param(
    [ValidateSet("Install", "Remove")]
    [string]$Action = "Install",
    [ValidateSet("Package", "Source")]
    [string]$Mode = "Package",
    [string]$ProjectPath = "",
    [string]$PluginDirectory = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$ProjectDirectory = Split-Path -Parent $ProjectPath
$DefaultSourceDirectory = Join-Path $ToolRoot "Plugin\UEAgentKit"
$DefaultPackageDirectory = Join-Path $ToolRoot "Build\Compiled\UEAgentKit"
$LinkPath = Join-Path $ProjectDirectory "Plugins\UEAgentKit"

if ([string]::IsNullOrWhiteSpace($PluginDirectory))
{
    $PluginDirectory = if ($Mode -eq "Package") { $DefaultPackageDirectory } else { $DefaultSourceDirectory }
}
$PluginDirectory = [System.IO.Path]::GetFullPath($PluginDirectory)

if ($Action -eq "Install")
{
    $PluginDescriptor = Join-Path $PluginDirectory "UEAgentKit.uplugin"
    Assert-UeakPath -Path $PluginDescriptor -Description "Plugin descriptor" -PathType File
    Ensure-UeakJunction -LinkPath $LinkPath -TargetPath $PluginDirectory | Out-Null

    Write-Host "Installed project plugin junction:"
    Write-Host "  Project: $ProjectPath"
    Write-Host "  Link   : $LinkPath"
    Write-Host "  Target : $PluginDirectory"
    Write-Host "  Mode   : $Mode"
    exit 0
}

$Removed = Remove-UeakJunction -LinkPath $LinkPath
if ($Removed)
{
    Write-Host "Removed project plugin junction: $LinkPath"
}
else
{
    Write-Host "Project plugin junction is not installed: $LinkPath"
}
