param(
    [string]$ProjectPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$ContentRoot = Join-Path (Split-Path -Parent $ProjectPath) "Content"
$SourceRoot = "E:\WorkSpace\ModelPreview\Content"

# Copies read-only animation retarget fixture assets from the ModelPreview
# project into the test project at identical /Game paths, so package
# references (skeleton, mesh, animation) keep resolving. ModelPreview is
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
    # The ModelPreview source is read-only, but the copies in the test project
    # must be writable so the editor can save fixture edits (e.g. material slots).
    attrib -R -S -H -D (Join-Path $TargetDirectory "*") /S /D 2>$null
    Get-ChildItem -LiteralPath $TargetDirectory -Recurse -File | ForEach-Object {
        if ($_.IsReadOnly) { $_.IsReadOnly = $false }
    }
    $CopiedCount++
}

Write-Host "Copied $CopiedCount fixture roots into $ContentRoot."
