param(
    [string]$EngineRoot = "",
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,
    [ValidateRange(30, 300)]
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath

$SubTests = @(
    @{ Name = "ScalarWrite"; Script = Join-Path $PSScriptRoot "TestMcpLiveWrite.ps1"; OutputDir = "Output\McpLiveWriteSmoke"; BackupDir = "Backups\McpLiveWriteSmoke" },
    @{ Name = "ReferenceWrite"; Script = Join-Path $PSScriptRoot "TestMcpLiveReferenceWrite.ps1"; OutputDir = "Output\McpLiveReferenceWriteSmoke"; BackupDir = "Backups\McpLiveReferenceWriteSmoke" },
    @{ Name = "StructuredWrite"; Script = Join-Path $PSScriptRoot "TestMcpLiveStructuredWrite.ps1"; OutputDir = "Output\McpLiveStructuredWriteSmoke"; BackupDir = "Backups\McpLiveStructuredWriteSmoke" },
    @{ Name = "MaterialWrite"; Script = Join-Path $PSScriptRoot "TestMcpLiveMaterialWrite.ps1"; OutputDir = "Output\McpLiveMaterialWriteSmoke"; BackupDir = "Backups\McpLiveMaterialWriteSmoke" }
)

$RegressionRoot = Join-Path $ToolRoot "Output\McpLiveWriteRegression"
if (Test-Path -LiteralPath $RegressionRoot)
{
    Remove-Item -LiteralPath $RegressionRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $RegressionRoot -Force | Out-Null

$FailedSubtests = @()
foreach ($SubTest in $SubTests)
{
    $Name = $SubTest.Name
    $Log = Join-Path $RegressionRoot "$Name.log"
    Write-Host "=== MCP Live Write regression sub-test: $Name ==="
    $ExitCode = 0
    try
    {
        & $SubTest.Script -EngineRoot $EngineRoot -ProjectPath $ProjectPath -StartupTimeoutSeconds $StartupTimeoutSeconds 2>&1 | Tee-Object -FilePath $Log
    }
    catch
    {
        $ExitCode = 1
        $_ | Out-String | Add-Content -LiteralPath $Log
    }
    if ($ExitCode -ne 0)
    {
        $FailedSubtests += $Name
        $Preserved = Join-Path $RegressionRoot "$Name-failed"
        New-Item -ItemType Directory -Path $Preserved -Force | Out-Null
        $SubOutput = Join-Path $ToolRoot $SubTest.OutputDir
        if (Test-Path -LiteralPath $SubOutput)
        {
            Copy-Item -LiteralPath $SubOutput -Destination $Preserved -Recurse -Force
        }
        $SubBackup = Join-Path $ToolRoot $SubTest.BackupDir
        if (Test-Path -LiteralPath $SubBackup)
        {
            Copy-Item -LiteralPath $SubBackup -Destination (Join-Path $Preserved ([System.IO.Path]::GetFileName($SubBackup))) -Recurse -Force
        }
        $Summary = @(
            "Sub-test $Name failed.",
            "Sub-test log: $Log",
            "Preserved output: $(Join-Path $Preserved ([System.IO.Path]::GetFileName($SubOutput)))",
            "Preserved backups: $(Join-Path $Preserved ([System.IO.Path]::GetFileName($SubBackup)))"
        )
        $Summary | Set-Content -LiteralPath (Join-Path $Preserved "failure-summary.txt")
        Write-Host "=== MCP Live Write regression sub-test: $Name FAILED (logs preserved under $Preserved) ==="
    }
    else
    {
        Write-Host "=== MCP Live Write regression sub-test: $Name PASSED ==="
    }
    Start-Sleep -Seconds 3
}

if ($FailedSubtests.Count -gt 0)
{
    Write-Host "MCP Live Write regression FAILED sub-tests: $($FailedSubtests -join ', ')"
    throw "MCP Live Write regression failed: $($FailedSubtests -join ', ') - logs preserved under $RegressionRoot"
}
Write-Host "MCP Live Write regression passed (ScalarWrite, ReferenceWrite, StructuredWrite, MaterialWrite)."
