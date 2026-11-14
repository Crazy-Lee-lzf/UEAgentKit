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

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$DefaultSourceDirectory = Join-Path $ToolRoot "Plugin\UEAgentKit"
$DefaultPackageDirectory = Join-Path $ToolRoot "Build\Compiled\UEAgentKit"
$Destination = Join-Path $EngineRoot "Engine\Plugins\Developer\UEAgentKit"

if ([string]::IsNullOrWhiteSpace($PluginDirectory))
{
    $PluginDirectory = if ($Mode -eq "Package") { $DefaultPackageDirectory } else { $DefaultSourceDirectory }
}
$PluginDirectory = [System.IO.Path]::GetFullPath($PluginDirectory)

$AllowedInstallRoot = Join-Path $EngineRoot "Engine\Plugins\Developer"
$MarkerName = ".ueak-managed-copy"
$Marker = Join-Path $Destination $MarkerName

if ($Action -eq "Install")
{
    Assert-UeakPath -Path (Join-Path $PluginDirectory "UEAgentKit.uplugin") -Description "Plugin descriptor" -PathType File
    Assert-UeakPlainTree -Path $PluginDirectory
    Assert-UeakUnderDirectory -Path $Destination -AllowedRoot $AllowedInstallRoot
    if (Test-Path -LiteralPath $Destination)
    {
        throw "Destination already exists. Refusing to modify an existing plugin installation: $Destination"
    }
    New-Item -ItemType Directory -Path $AllowedInstallRoot -Force | Out-Null
    Copy-Item -LiteralPath $PluginDirectory -Destination $Destination -Recurse -Force
    [System.IO.File]::WriteAllText($Marker, "UEAgentKit managed plugin copy", [System.Text.UTF8Encoding]::new($false))
    Write-Host "Installed engine plugin by copying files: $Destination"
    exit 0
}

if (!(Test-Path -LiteralPath $Destination))
{
    Write-Host "No managed plugin copy exists: $Destination"
    exit 0
}
Assert-UeakPlainTree -Path $Destination
if (!(Test-Path -LiteralPath $Marker -PathType Leaf))
{
    throw "Refusing to remove an unmanaged plugin installation (marker absent): $Destination"
}
Remove-UeakSafeDirectory -Path $Destination -AllowedRoot $AllowedInstallRoot
Write-Host "Removed managed engine plugin copy: $Destination"
