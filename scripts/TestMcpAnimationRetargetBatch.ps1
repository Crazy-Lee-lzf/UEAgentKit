param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [ValidateRange(30, 300)]
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$ProjectName = [System.IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$ProjectDirectory = Split-Path -Parent $ProjectPath
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$CatalogScript = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$ClientScript = Join-Path $ToolRoot "tests\integration\mcp_live_retarget_batch_smoke.py"
$UnrealEditor = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
$CompiledPlugin = Join-Path $ToolRoot "Build\Compiled\UEAgentKit\Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll"
foreach ($Required in @($VenvPython, $CatalogScript, $ClientScript, $UnrealEditor, $CompiledPlugin))
{
    Assert-UeakPath -Path $Required -Description ([System.IO.Path]::GetFileName($Required)) -PathType File
}

$Output = Join-Path $ToolRoot "Output\McpAnimationRetargetBatchSmoke"
if (Test-Path -LiteralPath $Output)
{
    Remove-Item -LiteralPath $Output -Recurse -Force
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$BackupRoot = Join-Path $ToolRoot "Backups\McpAnimationRetargetBatchSmoke"
if (Test-Path -LiteralPath $BackupRoot)
{
    Remove-Item -LiteralPath $BackupRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null

$RevisionExport = Join-Path $Output "Revision"
$Database = Join-Path $Output "Index\ueak.sqlite3"
$Policy = Join-Path $Output "policy.json"
$WorkRoot = Join-Path $Output "Workflow"
$ErrorLog = Join-Path $Output "Logs\mcp-stderr.log"
$SessionMarker = Join-Path $Output "session-initialized.marker"
$EditorStdout = Join-Path $Output "Logs\Editor-stdout.log"
$EditorStderr = Join-Path $Output "Logs\Editor-stderr.log"
$DescriptorPath = Join-Path $ProjectDirectory "Saved\UEAgentKit\EditorBridge.json"
$EditorProcess = $null
$Succeeded = $false

function Write-Utf8Json
{
    param([string]$Path, [object]$Value)
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 20) + "`r`n"),
        [System.Text.UTF8Encoding]::new($false))
}

function Read-BridgeDescriptor
{
    if (!(Test-Path -LiteralPath $DescriptorPath))
    {
        return $null
    }
    try
    {
        return [System.IO.File]::ReadAllText(
            $DescriptorPath,
            [System.Text.UTF8Encoding]::new($false)) | ConvertFrom-Json
    }
    catch
    {
        return $null
    }
}

try
{
    $ExistingDescriptor = Read-BridgeDescriptor
    if ($null -ne $ExistingDescriptor)
    {
        $ExistingProcess = Get-Process -Id ([int]$ExistingDescriptor.processId) -ErrorAction SilentlyContinue
        if ($null -ne $ExistingProcess)
        {
            throw "A Live Editor Bridge is already active for this test project. Close that Editor first."
        }
        Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Preparing retarget fixtures..."
    & (Join-Path $PSScriptRoot "PrepareRetargetFixtures.ps1") -ProjectPath $ProjectPath

    & $CatalogScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Asset "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple" `
        -Output $RevisionExport
    if ($LASTEXITCODE -ne 0)
    {
        throw "Retarget Batch Revision Export (source) failed with exit code $LASTEXITCODE"
    }
    & $CatalogScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Asset "/Game/Characters/XinYueHu/Mesh/SK_XinYueHu" `
        -Output $RevisionExport
    if ($LASTEXITCODE -ne 0)
    {
        throw "Retarget Batch Revision Export (target) failed with exit code $LASTEXITCODE"
    }

    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Database)) -Force | Out-Null
    & $VenvPython (Join-Path $PSScriptRoot "ue-agent.py") index build $RevisionExport --database $Database --force --project-key $ProjectName | Out-Host
    if ($LASTEXITCODE -ne 0)
    {
        throw "Live Retarget Batch smoke index build failed with exit code $LASTEXITCODE"
    }

    $PolicyValue = [ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @("/Game/UEAgentKitRetargetTests", "/Game/Characters")
        allowedReferenceRoots = @("/Game/Characters")
        retargetCapabilities = @("retarget.inspect", "retarget.plan", "retarget.configure", "retarget.batch")
        requireRevision = $true
        rejectDirtyPackages = $false
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    }
    Write-Utf8Json -Path $Policy -Value $PolicyValue

    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($EditorStdout)) -Force | Out-Null
    $EditorProcess = Start-Process `
        -FilePath $UnrealEditor `
        -ArgumentList @(
            $ProjectPath,
            "-unattended",
            "-nosplash",
            "-NoSound",
            "-NoP4",
            "-stdout",
            "-FullStdOutLogOutput") `
        -PassThru `
        -RedirectStandardOutput $EditorStdout `
        -RedirectStandardError $EditorStderr

    $Deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $Ready = $false
    while ((Get-Date) -lt $Deadline)
    {
        if ($EditorProcess.HasExited)
        {
            throw "Unreal Editor exited before publishing the Live Editor Bridge descriptor."
        }
        $Descriptor = Read-BridgeDescriptor
        if ($null -ne $Descriptor -and
            [int]$Descriptor.processId -eq $EditorProcess.Id -and
            [int]$Descriptor.port -gt 0)
        {
            $Ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
        $EditorProcess.Refresh()
    }
    if (!$Ready)
    {
        throw "Timed out waiting for the Live Editor Bridge descriptor."
    }

    $ClientAttempt = 0
    $ClientExitCode = 1
    while ($ClientAttempt -lt 2)
    {
        $ClientAttempt++
        Remove-Item -LiteralPath $SessionMarker -Force -ErrorAction SilentlyContinue
        & $VenvPython $ClientScript `
            --engine-root $EngineRoot `
            --project $ProjectPath `
            --database $Database `
            --policy $Policy `
            --revision-export $RevisionExport `
            --work-root $WorkRoot `
            --backup-root $BackupRoot `
            --error-log $ErrorLog `
            --session-marker $SessionMarker
        $ClientExitCode = $LASTEXITCODE
        if ($ClientExitCode -eq 0)
        {
            break
        }
        if ($ClientAttempt -lt 2 -and !(Test-Path -LiteralPath $SessionMarker))
        {
            Write-Warning "MCP client failed before an MCP session was established; retrying the client startup once."
            continue
        }
        break
    }
    if ($ClientExitCode -ne 0)
    {
        throw "MCP Live Editor Animation Retarget Batch smoke test failed with exit code $ClientExitCode"
    }
    $Succeeded = $true
}
finally
{
    if ($null -ne $EditorProcess)
    {
        Stop-Process -Id $EditorProcess.Id -Force -ErrorAction SilentlyContinue
        $ExitDeadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $ExitDeadline)
        {
            if ($null -eq (Get-Process -Id $EditorProcess.Id -ErrorAction SilentlyContinue))
            {
                break
            }
            Start-Sleep -Milliseconds 250
        }
    }
    $Descriptor = Read-BridgeDescriptor
    if ($null -ne $Descriptor -and $null -ne $EditorProcess -and [int]$Descriptor.processId -eq $EditorProcess.Id)
    {
        Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue
    }
}

if (!$Succeeded)
{
    throw "MCP Live Editor Animation Retarget Batch smoke test did not complete successfully."
}
Write-Host "MCP Live Editor Animation Retarget Batch smoke test passed."
