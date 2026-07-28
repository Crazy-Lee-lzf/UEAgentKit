param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [Parameter(Mandatory = $true)]
    [string]$Manifest,
    [Parameter(Mandatory = $true)]
    [string]$Policy,
    [Parameter(Mandatory = $true)]
    [string]$BackupRoot,
    [ValidateSet("DryRun", "Commit")]
    [string]$Mode = "DryRun",
    [string]$Report = "",
    [string]$VerificationOutput = "",
    [string]$VerificationReport = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$AgentCmd = Join-Path $PSScriptRoot "ue-agent.cmd"
$RunAssetCatalog = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"

Assert-UeakPath -Path $AgentCmd -Description "ue-agent.cmd" -PathType File
Assert-UeakPath -Path $RunAssetCatalog -Description "RunAssetCatalog.ps1" -PathType File

$Manifest = [System.IO.Path]::GetFullPath($Manifest)
$Policy = [System.IO.Path]::GetFullPath($Policy)
$BackupRoot = [System.IO.Path]::GetFullPath($BackupRoot)
Assert-UeakPath -Path $Manifest -Description "Backup manifest" -PathType File
Assert-UeakPath -Path $Policy -Description "Write policy JSON" -PathType File
Assert-UeakPath -Path $BackupRoot -Description "Backup root" -PathType Directory

if ([string]::IsNullOrWhiteSpace($Report))
{
    $Report = Join-Path $ToolRoot "Output\Rollback\rollback-report.json"
}
else
{
    $Report = [System.IO.Path]::GetFullPath($Report)
}
New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Report)) -Force | Out-Null

if ($Mode -eq "Commit")
{
    try
    {
        $RunningEditors = @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    ($_.Name -eq "UnrealEditor.exe" -or $_.Name -eq "UnrealEditor-Cmd.exe") -and
                    ![string]::IsNullOrWhiteSpace([string]$_.CommandLine) -and
                    ([string]$_.CommandLine).IndexOf(
                        $ProjectPath,
                        [System.StringComparison]::OrdinalIgnoreCase
                    ) -ge 0
                }
        )
    }
    catch
    {
        throw "Unable to verify that the target Unreal project is closed: $($_.Exception.Message)"
    }
    if ($RunningEditors.Count -gt 0)
    {
        $ProcessIds = ($RunningEditors | ForEach-Object { $_.ProcessId }) -join ", "
        throw "Rollback Commit requires the target Unreal project to be closed. Running process IDs: $ProcessIds"
    }
}

Write-Host "Validating rollback..."
Write-Host "Project   : $ProjectPath"
Write-Host "Mode      : $Mode"
Write-Host "Manifest  : $Manifest"
Write-Host "Policy    : $Policy"
Write-Host "BackupRoot: $BackupRoot"
Write-Host "Report    : $Report"

& $AgentCmd patch rollback `
    --manifest $Manifest `
    --policy $Policy `
    --project $ProjectPath `
    --backup-root $BackupRoot `
    --mode $Mode `
    --report $Report
if ($LASTEXITCODE -ne 0)
{
    throw "Rollback $Mode failed with exit code $LASTEXITCODE"
}

if ($Mode -eq "Commit")
{
    $Rollback = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
    if (!$Rollback.restored)
    {
        throw "Rollback report does not confirm a completed restore."
    }
    if ([string]::IsNullOrWhiteSpace($VerificationOutput))
    {
        $VerificationOutput = Join-Path $ToolRoot ("Output\Rollback\{0}\Verify" -f [string]$Rollback.rollbackId)
    }
    else
    {
        $VerificationOutput = [System.IO.Path]::GetFullPath($VerificationOutput)
    }
    if ([string]::IsNullOrWhiteSpace($VerificationReport))
    {
        $VerificationReport = Join-Path ([System.IO.Path]::GetDirectoryName($Report)) "rollback-verification.json"
    }
    else
    {
        $VerificationReport = [System.IO.Path]::GetFullPath($VerificationReport)
    }
    New-Item -ItemType Directory -Path $VerificationOutput -Force | Out-Null
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($VerificationReport)) -Force | Out-Null

    $AssetPackage = ([string]$Rollback.assetPath).Split(".")[0]
    $ManifestValue = [IO.File]::ReadAllText($Manifest, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $CatalogArguments = @{
        EngineRoot = $EngineRoot
        ProjectPath = $ProjectPath
        Asset = $AssetPackage
        Output = $VerificationOutput
    }
    if ([string]$ManifestValue.assetClass -eq "/Script/Engine.Blueprint")
    {
        $CatalogArguments.IncludeBlueprints = $true
    }
    Write-Host "Reloading restored asset in an independent Unreal process..."
    & $RunAssetCatalog @CatalogArguments
    if ($LASTEXITCODE -ne 0)
    {
        throw "Independent rollback export failed with exit code $LASTEXITCODE"
    }

    & $AgentCmd patch verify-rollback `
        --rollback-report $Report `
        --export $VerificationOutput `
        --report $VerificationReport
    if ($LASTEXITCODE -ne 0)
    {
        throw "Rollback verification failed with exit code $LASTEXITCODE. The pre-rollback safety backup remains in $($Rollback.preRollbackBackupPath)."
    }
    Write-Host "Verification: $VerificationReport"
}

Write-Host "Rollback completed: $Report"
