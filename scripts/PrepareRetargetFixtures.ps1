param(
    [string]$ProjectPath = "",
    [string]$SourceContentRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$ContentRoot = Join-Path (Split-Path -Parent $ProjectPath) "Content"

# The fixture source is an external, read-only project supplied by the caller.
# No machine-specific location is embedded in the script.
if ([string]::IsNullOrWhiteSpace($SourceContentRoot))
{
    $SourceContentRoot = $env:UEAK_RETARGET_SOURCE
}
if ([string]::IsNullOrWhiteSpace($SourceContentRoot))
{
    throw "Retarget fixture source Content root was not provided. Pass -SourceContentRoot or set UEAK_RETARGET_SOURCE."
}
$SourceRoot = [System.IO.Path]::GetFullPath($SourceContentRoot)
Assert-UeakPath -Path $SourceRoot -Description "Retarget fixture source Content" -PathType Directory

# Copies read-only animation retarget fixture assets from the external source
# project into the test project at identical /Game paths, so package
# references (skeleton, mesh, animation) keep resolving. The source project is
# never modified; only the test project Content is written.
$Copies = @(
    @{ Source = "Characters\Mannequins"; Relative = "Characters\Mannequins" },
    @{ Source = "Characters\XinYueHu"; Relative = "Characters\XinYueHu" }
)

$CopiedCount = 0
foreach ($Copy in $Copies)
{
    $SourceDirectory = Join-Path $SourceRoot $Copy.Source
    if (!(Test-Path -LiteralPath $SourceDirectory))
    {
        throw "Fixture source directory missing: $SourceDirectory"
    }
    $TargetDirectory = Join-Path $ContentRoot $Copy.Relative
    New-Item -ItemType Directory -Path $TargetDirectory -Force | Out-Null
    Copy-Item -Path (Join-Path $SourceDirectory "*") -Destination $TargetDirectory -Recurse -Force
    # The external source is read-only, but the copies in the test project
    # must be writable so the editor can save fixture edits (e.g. material slots).
    attrib -R -S -H -D (Join-Path $TargetDirectory "*") /S /D 2>$null
    Get-ChildItem -LiteralPath $TargetDirectory -Recurse -File | ForEach-Object {
        if ($_.IsReadOnly) { $_.IsReadOnly = $false }
    }
    $CopiedCount++
}

Write-Host "Copied $CopiedCount fixture roots into $ContentRoot."
