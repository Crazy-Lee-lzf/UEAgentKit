param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [Parameter(Mandatory = $true)]
    [string]$Plan,
    [ValidateSet("Create", "Reset")]
    [string]$Mode = "Reset",
    [string]$Report = "",
    [string]$ValidationReport = "",
    [string]$VerificationOutput = "",
    [string]$VerificationReport = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
$AgentCmd = Join-Path $PSScriptRoot "ue-agent.cmd"
$CatalogScript = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"

Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File
Assert-UeakPath -Path $AgentCmd -Description "ue-agent.cmd" -PathType File
Assert-UeakPath -Path $CatalogScript -Description "RunAssetCatalog.ps1" -PathType File

$Plan = [System.IO.Path]::GetFullPath($Plan)
Assert-UeakPath -Path $Plan -Description "Fixture plan JSON" -PathType File

if ([string]::IsNullOrWhiteSpace($Report))
{
    $Report = Join-Path $ToolRoot "Output\WriteFixtures\fixture-report.json"
}
else
{
    $Report = [System.IO.Path]::GetFullPath($Report)
}
if ([string]::IsNullOrWhiteSpace($ValidationReport))
{
    $ValidationReport = Join-Path ([System.IO.Path]::GetDirectoryName($Report)) "validation-report.json"
}
else
{
    $ValidationReport = [System.IO.Path]::GetFullPath($ValidationReport)
}
if ([string]::IsNullOrWhiteSpace($VerificationOutput))
{
    $VerificationOutput = Join-Path ([System.IO.Path]::GetDirectoryName($Report)) "Reload"
}
else
{
    $VerificationOutput = [System.IO.Path]::GetFullPath($VerificationOutput)
}
if ([string]::IsNullOrWhiteSpace($VerificationReport))
{
    $VerificationReport = Join-Path ([System.IO.Path]::GetDirectoryName($Report)) "verification-report.json"
}
else
{
    $VerificationReport = [System.IO.Path]::GetFullPath($VerificationReport)
}

$SafeVerificationRoot = [System.IO.Path]::GetFullPath((Join-Path $ToolRoot "Output"))
$SafeVerificationPrefix = $SafeVerificationRoot.TrimEnd([char]'\', [char]'/') + [System.IO.Path]::DirectorySeparatorChar
if (!$VerificationOutput.StartsWith($SafeVerificationPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
    $VerificationOutput.Equals($SafeVerificationRoot, [System.StringComparison]::OrdinalIgnoreCase))
{
    throw "VerificationOutput must be a child directory below the tool Output directory: $VerificationOutput"
}

$VerificationCursor = [System.IO.DirectoryInfo]$VerificationOutput
while ($null -ne $VerificationCursor)
{
    if ($VerificationCursor.Exists -and
        (($VerificationCursor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0))
    {
        throw "VerificationOutput path must not traverse a Junction or symbolic link: $($VerificationCursor.FullName)"
    }
    if ($VerificationCursor.FullName.Equals($SafeVerificationRoot, [System.StringComparison]::OrdinalIgnoreCase))
    {
        break
    }
    $VerificationCursor = $VerificationCursor.Parent
}
if (Test-Path -LiteralPath $VerificationOutput)
{
    $NestedReparsePoint = Get-ChildItem -LiteralPath $VerificationOutput -Force -Recurse -ErrorAction Stop |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 } |
        Select-Object -First 1
    if ($null -ne $NestedReparsePoint)
    {
        throw "VerificationOutput contains a Junction or symbolic link and cannot be recursively cleared: $($NestedReparsePoint.FullName)"
    }
}

$ProtectedPaths = @($Plan, $Report, $ValidationReport, $VerificationReport)
$VerificationPrefix = $VerificationOutput.TrimEnd([char]'\', [char]'/') + [System.IO.Path]::DirectorySeparatorChar
foreach ($ProtectedPath in $ProtectedPaths)
{
    if ($ProtectedPath.Equals($VerificationOutput, [System.StringComparison]::OrdinalIgnoreCase) -or
        $ProtectedPath.StartsWith($VerificationPrefix, [System.StringComparison]::OrdinalIgnoreCase))
    {
        throw "VerificationOutput would remove a fixture input or report: $ProtectedPath"
    }
}
$UniqueProtectedPaths = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
foreach ($ProtectedPath in $ProtectedPaths)
{
    if (!$UniqueProtectedPaths.Add($ProtectedPath))
    {
        throw "Fixture input and report paths must be unique: $ProtectedPath"
    }
}

foreach ($OutputPath in @($Report, $ValidationReport, $VerificationReport))
{
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($OutputPath)) -Force | Out-Null
}

Write-Host "Validating write-fixture plan..."
& $AgentCmd fixtures validate --plan $Plan --report $ValidationReport
if ($LASTEXITCODE -ne 0)
{
    throw "Fixture-plan validation failed with exit code $LASTEXITCODE"
}
$Validation = Get-Content -LiteralPath $ValidationReport -Raw | ConvertFrom-Json
$Root = [string]$Validation.root
$PlanRevision = [string]$Validation.planRevision
if (!$Validation.valid -or [string]::IsNullOrWhiteSpace($Root) -or
    $PlanRevision -notmatch '^sha256:[0-9a-f]{64}$')
{
    throw "Fixture-plan validation did not return a valid root and SHA-256 revision."
}

$Arguments = @(
    $ProjectPath,
    "-run=WriteFixturePlan",
    "-Plan=$Plan",
    "-ExpectedPlanRevision=$PlanRevision",
    "-Report=$Report",
    "-Mode=$Mode",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NoSound",
    "-NullRHI",
    "-stdout",
    "-FullStdOutLogOutput"
)

Write-Host "Running WriteFixturePlan..."
Write-Host "Engine    : $EngineRoot"
Write-Host "Project   : $ProjectPath"
Write-Host "Mode      : $Mode"
Write-Host "Plan      : $Plan"
Write-Host "Report    : $Report"
& $EditorCmd @Arguments
if ($LASTEXITCODE -ne 0)
{
    throw "WriteFixturePlan failed with exit code $LASTEXITCODE"
}

$FixtureReport = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
if (!$FixtureReport.valid -or $FixtureReport.status -ne "completed")
{
    throw "WriteFixturePlan report did not describe a completed plan."
}

if (Test-Path -LiteralPath $VerificationOutput)
{
    Remove-Item -LiteralPath $VerificationOutput -Recurse -Force
}
Write-Host "Reloading fixtures in an independent Unreal process..."
& $CatalogScript `
    -EngineRoot $EngineRoot `
    -ProjectPath $ProjectPath `
    -Root $Root `
    -Output $VerificationOutput `
    -IncludeBlueprints
if ($LASTEXITCODE -ne 0)
{
    throw "Independent fixture export failed with exit code $LASTEXITCODE"
}

& $AgentCmd fixtures verify `
    --fixture-report $Report `
    --export $VerificationOutput `
    --report $VerificationReport
if ($LASTEXITCODE -ne 0)
{
    throw "Independent fixture verification failed with exit code $LASTEXITCODE"
}

Write-Host "Fixture plan completed and verified."
Write-Host "Report      : $Report"
Write-Host "Verification: $VerificationReport"
