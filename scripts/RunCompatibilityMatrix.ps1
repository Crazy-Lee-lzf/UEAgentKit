param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$CasesPath = "",
    [string]$OutputRoot = "",
    [ValidateSet("index", "structure", "logic", "defaults", "full", "ai")]
    [string]$Profile = "full",
    [ValidateSet("json", "bpctx", "both")]
    [string]$Format = "both"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-BctToolRoot
$EngineRoot = Resolve-BctEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-BctProjectPath -ProjectPath $ProjectPath
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
Assert-BctPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File

if ([string]::IsNullOrWhiteSpace($CasesPath))
{
    $CasesPath = $env:BCT_COMPATIBILITY_CASES
}
if ([string]::IsNullOrWhiteSpace($CasesPath))
{
    throw "Compatibility cases were not provided. Pass -CasesPath or set BCT_COMPATIBILITY_CASES."
}
$CasesPath = [System.IO.Path]::GetFullPath($CasesPath)
Assert-BctPath -Path $CasesPath -Description "Compatibility cases JSON" -PathType File

if ([string]::IsNullOrWhiteSpace($OutputRoot))
{
    $OutputRoot = Join-Path $ToolRoot "Output\CompatibilityMatrix"
}
else
{
    $OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
}

$Cases = @(Get-Content -LiteralPath $CasesPath -Raw -Encoding UTF8 | ConvertFrom-Json)
if ($Cases.Count -eq 0)
{
    throw "Compatibility cases JSON contains no cases: $CasesPath"
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$Results = @()

foreach ($Case in $Cases)
{
    $Name = [string]$Case.name
    $Asset = [string]$Case.asset
    if ([string]::IsNullOrWhiteSpace($Name) -or [string]::IsNullOrWhiteSpace($Asset))
    {
        throw "Each compatibility case must contain non-empty 'name' and 'asset' fields."
    }

    $SafeName = $Name -replace '[^A-Za-z0-9_.-]', '_'
    $CaseOutput = Join-Path $OutputRoot $SafeName
    $LogPath = Join-Path $CaseOutput "export.log"
    New-Item -ItemType Directory -Path $CaseOutput -Force | Out-Null

    $Arguments = @(
        $ProjectPath,
        "-run=BlueprintContextExport",
        "-Asset=$Asset",
        "-Output=$CaseOutput",
        "-Profile=$Profile",
        "-Format=$Format",
        "-IncludeUnchangedDefaults",
        "-unattended",
        "-nop4",
        "-nosplash",
        "-NoSound",
        "-NullRHI",
        "-stdout",
        "-FullStdOutLogOutput"
    )

    Write-Host "TEST_BEGIN $Name $Asset"
    & $EditorCmd @Arguments *> $LogPath
    $ExitCode = $LASTEXITCODE

    $ManifestPath = Join-Path $CaseOutput "manifest.json"
    $ManifestValid = $false
    $SuccessCount = 0
    $FailureCount = 0
    if (Test-Path -LiteralPath $ManifestPath -PathType Leaf)
    {
        try
        {
            $Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $ManifestValid = $true
            $SuccessCount = [int]$Manifest.successCount
            $FailureCount = [int]$Manifest.failureCount
        }
        catch
        {
            $ManifestValid = $false
        }
    }

    $CanonicalFiles = @(Get-ChildItem -LiteralPath (Join-Path $CaseOutput "canonical") -Recurse -File -Filter *.json -ErrorAction SilentlyContinue)
    $BpctxFiles = @(Get-ChildItem -LiteralPath (Join-Path $CaseOutput "bpctx") -Recurse -File -Filter *.bpctx -ErrorAction SilentlyContinue)

    $Results += [PSCustomObject]@{
        Name = $Name
        Asset = $Asset
        ExitCode = $ExitCode
        ManifestValid = $ManifestValid
        SuccessCount = $SuccessCount
        FailureCount = $FailureCount
        CanonicalCount = $CanonicalFiles.Count
        BpctxCount = $BpctxFiles.Count
        LogPath = $LogPath
    }

    Write-Host "TEST_END $Name exit=$ExitCode success=$SuccessCount failure=$FailureCount json=$($CanonicalFiles.Count) bpctx=$($BpctxFiles.Count)"
}

$SummaryPath = Join-Path $OutputRoot "summary.json"
$SummaryJson = $Results | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($SummaryPath, $SummaryJson, [System.Text.UTF8Encoding]::new($false))
$Results | Format-Table -AutoSize
Write-Host "SUMMARY=$SummaryPath"

$FailedResults = @($Results | Where-Object { $_.ExitCode -ne 0 -or !$_.ManifestValid -or $_.SuccessCount -ne 1 })
if ($FailedResults.Count -gt 0)
{
    exit 1
}

exit 0
