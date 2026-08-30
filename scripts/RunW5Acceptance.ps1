param(
    [ValidateSet("RunResident", "RunCold", "Summarize", "ResetDirectHost", "GeneratePerfProject")]
    [string]$Action = "Summarize",
    [string]$Scenario = "R5",
    [int]$SampleIndex = 1,
    [string]$CacheState = "WarmLoaded",
    [string]$LaunchIndex = 0,
    [string]$RunId = "",
    [string]$OutputDir = "",
    [string]$Database = "",
    [string]$RevisionExport = "",
    [string]$Policy = "",
    [string]$WorkRoot = "",
    [string]$BackupRoot = "",
    [string]$ProjectPath = "",
    [string]$EngineRoot = "",
    [string]$PatchPath = "",
    [string]$Attempts = "",
    [string]$FixturePlan = "",
    [string]$FixtureReport = "",
    [string]$VerificationReport = "",
    [string]$VerificationOutput = "",
    [string]$PerfProjectPath = "",
    [string]$PerfAction = "ValidateFixture"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $ToolRoot "benchmarks\w5\runner.py"
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot

if ([string]::IsNullOrWhiteSpace($RunId))
{
    $RunId = "w5-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))"
}
if ([string]::IsNullOrWhiteSpace($OutputDir))
{
    $OutputDir = Join-Path $ToolRoot "Output\W5Acceptance\$RunId"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

if ($Action -eq "RunResident")
{
    if ([string]::IsNullOrWhiteSpace($Database) -or [string]::IsNullOrWhiteSpace($RevisionExport) -or
        [string]::IsNullOrWhiteSpace($Policy) -or [string]::IsNullOrWhiteSpace($WorkRoot) -or
        [string]::IsNullOrWhiteSpace($BackupRoot) -or [string]::IsNullOrWhiteSpace($ProjectPath))
    {
        throw "RunResident requires -Database -RevisionExport -Policy -WorkRoot -BackupRoot -ProjectPath."
    }
    $ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
    $Report = Join-Path $OutputDir ("attempt-{0}-{1}-{2}.json" -f $Scenario, $SampleIndex, $CacheState)
    & $VenvPython $Runner run-resident `
        --scenario $Scenario `
        --sample-index $SampleIndex `
        --cache-state $CacheState `
        --run-id $RunId `
        --database $Database `
        --revision-export $RevisionExport `
        --policy $Policy `
        --work-root $WorkRoot `
        --backup-root $BackupRoot `
        --project $ProjectPath `
        --engine-root $EngineRoot `
        --output $Report
    if ($LASTEXITCODE -ne 0) { throw "W5 resident run failed with exit code $LASTEXITCODE" }
    Write-Host "Report: $Report"
}
elseif ($Action -eq "RunCold")
{
    if ([string]::IsNullOrWhiteSpace($PatchPath) -or [string]::IsNullOrWhiteSpace($Policy) -or
        [string]::IsNullOrWhiteSpace($RevisionExport) -or [string]::IsNullOrWhiteSpace($ProjectPath))
    {
        throw "RunCold requires -PatchPath -Policy -RevisionExport -ProjectPath."
    }
    $ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
    $Report = Join-Path $OutputDir ("cold-{0}-{1}-{2}.json" -f $Scenario, $SampleIndex, $LaunchIndex)
    & $VenvPython $Runner run-cold `
        --scenario $Scenario `
        --sample-index $SampleIndex `
        --launch-index $LaunchIndex `
        --run-id $RunId `
        --patch-path $PatchPath `
        --policy $Policy `
        --revision-export $RevisionExport `
        --project $ProjectPath `
        --engine-root $EngineRoot `
        --output $Report
    if ($LASTEXITCODE -ne 0) { throw "W5 cold run failed with exit code $LASTEXITCODE" }
    Write-Host "Report: $Report"
}
elseif ($Action -eq "Summarize")
{
    if ([string]::IsNullOrWhiteSpace($Attempts))
    {
        throw "Summarize requires -Attempts (space-separated paths)."
    }
    $AttemptPaths = @($Attempts -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $Summary = Join-Path $OutputDir "summary.json"
    & $VenvPython $Runner summarize --attempts $AttemptPaths --output $Summary
    if ($LASTEXITCODE -ne 0) { throw "W5 summarize failed with exit code $LASTEXITCODE" }
    Write-Host "Summary: $Summary"
}
elseif ($Action -eq "ResetDirectHost")
{
    if ([string]::IsNullOrWhiteSpace($ProjectPath))
    {
        $ProjectPath = Join-Path $ToolRoot "Build\DirectHost\HostProject.uproject"
    }
    if ([string]::IsNullOrWhiteSpace($FixturePlan))
    {
        $FixturePlan = Join-Path $ToolRoot "tests\fixtures\multi_operation_transaction_plan.json"
    }
    $ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
    $FixturePlan = [System.IO.Path]::GetFullPath($FixturePlan)
    if ([string]::IsNullOrWhiteSpace($FixtureReport))
    {
        $FixtureReport = Join-Path $OutputDir "reset\fixture-report.json"
    }
    if ([string]::IsNullOrWhiteSpace($VerificationReport))
    {
        $VerificationReport = Join-Path $OutputDir "reset\verification-report.json"
    }
    if ([string]::IsNullOrWhiteSpace($VerificationOutput))
    {
        $VerificationOutput = Join-Path $OutputDir "reset\Reload"
    }
    & (Join-Path $PSScriptRoot "RunWriteFixturePlan.ps1") `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Plan $FixturePlan `
        -Mode Reset `
        -Report $FixtureReport `
        -ValidationReport (Join-Path $OutputDir "reset\validation-report.json") `
        -VerificationOutput $VerificationOutput `
        -VerificationReport $VerificationReport
    if ($LASTEXITCODE -ne 0) { throw "DirectHost fixture reset failed with exit code $LASTEXITCODE" }
    Write-Host "Reset report : $FixtureReport"
    Write-Host "Verification : $VerificationReport"
}
elseif ($Action -eq "GeneratePerfProject")
{
    if ([string]::IsNullOrWhiteSpace($PerfProjectPath))
    {
        $PerfProjectPath = "E:\WorkSpace\UEAgentKitPerfProject"
    }
    $PerfUproject = Join-Path $PerfProjectPath "UEAgentKitPerfProject.uproject"
    if (!(Test-Path -LiteralPath $PerfUproject))
    {
        throw "Perf project does not exist yet: $PerfUproject"
    }
    $EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
    $PerfReport = Join-Path $OutputDir "perf-$PerfAction-report.json"
    & $EditorCmd $PerfUproject `
        "-run=PerformanceFixture" `
        "-Action=$PerfAction" `
        "-ProjectPath=$PerfProjectPath" `
        "-Report=$PerfReport" `
        "-unattended" `
        "-nop4" `
        "-nosplash" `
        "-NoSound" `
        "-NullRHI" `
        "-stdout" `
        "-FullStdOutLogOutput"
    if ($LASTEXITCODE -ne 0) { throw "Performance fixture action $PerfAction failed with exit code $LASTEXITCODE" }
    Write-Host "Perf report: $PerfReport"
}

Write-Host "W5 output directory: $OutputDir"
