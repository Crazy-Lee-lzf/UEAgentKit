param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [ValidateRange(30, 300)]
    [int]$StartupTimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$ProjectName = [System.IO.Path]::GetFileNameWithoutExtension($ProjectPath)
$ProjectDirectory = Split-Path -Parent $ProjectPath
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$FixtureScript = Join-Path $PSScriptRoot "RunWriteFixturePlan.ps1"
$CatalogScript = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$FixturePlan = Join-Path $ToolRoot "tests\fixtures\data_table_live_write_plan.json"
$ClientScript = Join-Path $ToolRoot "tests\integration\mcp_live_data_table_undo_save_smoke.py"
$VerifyScript = Join-Path $ToolRoot "tests\integration\mcp_live_data_table_reload_verify.py"
$UnrealEditor = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
$CompiledPlugin = Join-Path $ToolRoot "Build\Compiled\UEAgentKit\Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll"
foreach ($Required in @($VenvPython, $FixtureScript, $CatalogScript, $FixturePlan, $ClientScript, $VerifyScript, $UnrealEditor, $CompiledPlugin))
{
    Assert-UeakPath -Path $Required -Description ([System.IO.Path]::GetFileName($Required)) -PathType File
}

$Output = Join-Path $ToolRoot "Output\McpLiveDataTableUndoSaveSmoke"
if (Test-Path -LiteralPath $Output)
{
    Remove-Item -LiteralPath $Output -Recurse -Force
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$BackupRoot = Join-Path $ToolRoot "Backups\McpLiveDataTableUndoSaveSmoke"
if (Test-Path -LiteralPath $BackupRoot)
{
    Remove-Item -LiteralPath $BackupRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null

$FixtureDirectory = Join-Path $Output "Fixture"
$FixtureReport = Join-Path $FixtureDirectory "fixture-report.json"
$RevisionExport = Join-Path $Output "Revision"
$Database = Join-Path $Output "Index\ueak.sqlite3"
$Policy = Join-Path $Output "policy.json"
$WorkRoot = Join-Path $Output "Workflow"
$ErrorLog = Join-Path $Output "Logs\mcp-stderr.log"
$SessionMarker = Join-Path $Output "session-initialized.marker"
$ClientReport = Join-Path $Output "client-report.json"
$Reload = Join-Path $Output "Reload"
$ReloadReport = Join-Path $Output "reload-report.json"
$Summary = Join-Path $Output "summary.json"
$EditorStdout = Join-Path $Output "Logs\Editor-stdout.log"
$EditorStderr = Join-Path $Output "Logs\Editor-stderr.log"
$EditorPidFile = Join-Path $Output "editor-pid.txt"
$DescriptorPath = Join-Path $ProjectDirectory "Saved\UEAgentKit\EditorBridge.json"
$DataTableClass = "/Script/Engine.DataTable"
$DataTableRowStruct = "/Script/UEAgentKitEditor.UEAgentKitDataTableFixtureRow"
$DataTablesRoot = "/Game/UEAgentKitWriteTests/DataTables"
$SavedFixtureIds = @(
    "data-table-cell-asset",
    "data-table-remove-asset",
    "data-table-rename-asset"
)
$EditorProcess = $null
$FixtureHashes = @{}
$DatabaseHashBefore = ""
$RevisionExportHashBefore = ""
$Succeeded = $false
$Failure = ""

function Write-Utf8Json
{
    param([string]$Path, [object]$Value)
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Path)) -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 30) + "`r`n"),
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

function Get-RevisionExportHash
{
    param([string]$Root)
    $Items = Get-ChildItem -LiteralPath $Root -Filter "*.json" -File -Recurse |
        Sort-Object { $_.FullName.Substring($Root.Length).TrimStart('\', '/') }
    $Digest = [System.Security.Cryptography.SHA256]::Create()
    foreach ($Item in $Items)
    {
        $Relative = $Item.FullName.Substring($Root.Length).TrimStart('\', '/')
        $RelativeBytes = [System.Text.Encoding]::UTF8.GetBytes($Relative)
        [void]$Digest.TransformBlock($RelativeBytes, 0, $RelativeBytes.Length, $null, 0)
        [void]$Digest.TransformBlock([byte[]]@(1), 0, 1, $null, 0)
        $Bytes = [System.IO.File]::ReadAllBytes($Item.FullName)
        [void]$Digest.TransformBlock($Bytes, 0, $Bytes.Length, $null, 0)
    }
    [void]$Digest.TransformFinalBlock([byte[]]@(), 0, 0)
    return ([System.BitConverter]::ToString($Digest.Hash) -replace '-', '').ToLowerInvariant()
}

# The isolated test Python is a copy of a Store Python whose default temp
# directory is not SQLite-writable, and the shared DDC must be writable for
# commandlets launched while the Editor holds the project.  Both are host
# configuration: no product input or trackable file is involved.
$TmpRoot = Join-Path $Output "tmp"
New-Item -ItemType Directory -Path $TmpRoot -Force | Out-Null
$SharedDdc = Join-Path $ToolRoot "Build\DirectHost\SharedDDC"
New-Item -ItemType Directory -Path $SharedDdc -Force | Out-Null
$PreviousTmp = $env:TMP
$PreviousTemp = $env:TEMP
$PreviousSqliteTmp = $env:SQLITE_TMPDIR
$SavedLocalDdc = ${env:UE-LocalDataCachePath}
$SavedSharedDdc = ${env:UE-SharedDataCachePath}
$env:TMP = $TmpRoot
$env:TEMP = $TmpRoot
$env:SQLITE_TMPDIR = $TmpRoot
${env:UE-SharedDataCachePath} = $SharedDdc

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

    Write-Host "Resetting isolated DataTable fixtures..."
    & $FixtureScript `
        -EngineRoot $EngineRoot `
        -ProjectPath $ProjectPath `
        -Plan $FixturePlan `
        -Mode Reset `
        -Report $FixtureReport `
        -ValidationReport (Join-Path $FixtureDirectory "validation-report.json") `
        -VerificationOutput (Join-Path $FixtureDirectory "Reload") `
        -VerificationReport (Join-Path $FixtureDirectory "verification-report.json")
    if ($LASTEXITCODE -ne 0)
    {
        throw "DataTable fixture reset failed with exit code $LASTEXITCODE"
    }

    & $CatalogScript -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Root $DataTablesRoot -Output $RevisionExport
    if ($LASTEXITCODE -ne 0)
    {
        throw "DataTable Revision Export failed with exit code $LASTEXITCODE"
    }

    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($Database)) -Force | Out-Null
    & $VenvPython (Join-Path $PSScriptRoot "ue-agent.py") index build $RevisionExport --database $Database --force --project-key $ProjectName | Out-Host
    if ($LASTEXITCODE -ne 0)
    {
        throw "DataTable Undo/Save index build failed with exit code $LASTEXITCODE"
    }

    Write-Utf8Json -Path $Policy -Value ([ordered]@{
        schemaVersion = "1.0"
        validationEnabled = $true
        commitEnabled = $true
        allowedProjectNames = @($ProjectName)
        allowedAssetRoots = @($DataTablesRoot)
        allowedReferenceRoots = @()
        allowedReferenceClasses = @()
        allowedOperations = @(
            "setDataTableCell",
            "setDataTableRowFields",
            "addDataTableRow",
            "removeDataTableRow",
            "renameDataTableRow"
        )
        allowedAssetClasses = @($DataTableClass)
        allowedAssetProperties = @()
        allowedMaterialParameters = @()
        allowedDataTableFields = @(
            "$DataTableClass#$DataTableRowStruct#Count",
            "$DataTableClass#$DataTableRowStruct#Label",
            "$DataTableClass#$DataTableRowStruct#bEnabled"
        )
        requireRevision = $true
        rejectDirtyPackages = $true
        maxAssetsPerPatch = 1
        maxOperationsPerAsset = 1
        maxValueBytes = 4096
    })

    $Fixture = [System.IO.File]::ReadAllText($FixtureReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    foreach ($FixtureEntry in $Fixture.fixtures)
    {
        $PackageFile = [string]$FixtureEntry.packageFilename
        Assert-UeakPath -Path $PackageFile -Description "data table fixture package $($FixtureEntry.id)" -PathType File
        $FixtureHashes[[string]$FixtureEntry.id] = (Get-FileHash -LiteralPath $PackageFile -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $DatabaseHashBefore = (Get-FileHash -LiteralPath $Database -Algorithm SHA256).Hash.ToLowerInvariant()
    $RevisionExportHashBefore = Get-RevisionExportHash -Root $RevisionExport

    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($EditorStdout)) -Force | Out-Null
    # The Editor uses the machine's normal cached DDC (fast, already contains
    # engine/project shaders).  The MCP server and every commandlet it launches
    # get the writable shared DDC, because commandlets launched while the Editor
    # is running must not depend on the Editor-owned Zen node.
    Remove-Item Env:UE-LocalDataCachePath -ErrorAction SilentlyContinue
    Remove-Item Env:UE-SharedDataCachePath -ErrorAction SilentlyContinue
    $EditorProcess = Start-Process `
        -FilePath $UnrealEditor `
        -ArgumentList @(
            $ProjectPath,
            "-unattended",
            "-nosplash",
            "-NoSound",
            "-NoP4",
            "-ddc=NoZenLocalFallback",
            "-stdout",
            "-FullStdOutLogOutput") `
        -PassThru `
        -RedirectStandardOutput $EditorStdout `
        -RedirectStandardError $EditorStderr
    ${env:UE-SharedDataCachePath} = $SharedDdc
    [System.IO.File]::WriteAllText($EditorPidFile, "$($EditorProcess.Id)`r`n", [System.Text.UTF8Encoding]::new($false))
    Write-Host "Unreal Editor PID: $($EditorProcess.Id)"

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

    Remove-Item -LiteralPath $SessionMarker -Force -ErrorAction SilentlyContinue
    & $VenvPython $ClientScript `
        --engine-root $EngineRoot `
        --project $ProjectPath `
        --database $Database `
        --policy $Policy `
        --revision-export $RevisionExport `
        --work-root $WorkRoot `
        --backup-root $BackupRoot `
        --fixture-report $FixtureReport `
        --error-log $ErrorLog `
        --report $ClientReport `
        --session-marker $SessionMarker
    if ($LASTEXITCODE -ne 0)
    {
        throw "MCP Live Editor DataTable Undo/Save smoke failed with exit code $LASTEXITCODE"
    }

    if (Select-String -LiteralPath $EditorStdout -Pattern "Fatal error:|Assertion failed:|EXCEPTION_ACCESS_VIOLATION" -Quiet)
    {
        throw "The main Editor log contains a crash marker."
    }
    $Succeeded = $true
}
catch
{
    $Failure = $_.Exception.Message
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
    if ([string]::IsNullOrWhiteSpace($SavedLocalDdc))
    {
        Remove-Item Env:UE-LocalDataCachePath -ErrorAction SilentlyContinue
    }
    else
    {
        ${env:UE-LocalDataCachePath} = $SavedLocalDdc
    }
    if ([string]::IsNullOrWhiteSpace($SavedSharedDdc))
    {
        Remove-Item Env:UE-SharedDataCachePath -ErrorAction SilentlyContinue
    }
    else
    {
        ${env:UE-SharedDataCachePath} = $SavedSharedDdc
    }
    $env:TMP = $PreviousTmp
    $env:TEMP = $PreviousTemp
    $env:SQLITE_TMPDIR = $PreviousSqliteTmp
}
if (!$Succeeded)
{
    Write-Utf8Json -Path $Summary -Value ([ordered]@{
        schemaVersion = "1.0"
        test = "mcp-live-data-table-undo-save"
        succeeded = $false
        error = $Failure
        editorPid = if ($null -ne $EditorProcess) { $EditorProcess.Id } else { 0 }
        output = $Output
    })
    throw "MCP Live Editor DataTable Undo/Save smoke did not complete: $Failure"
}

# Fresh process, after the Editor has exited: reload every fixture from disk.
Write-Host "Reloading all DataTable fixtures in an independent Unreal process..."
& $CatalogScript -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Root $DataTablesRoot -Output $Reload
if ($LASTEXITCODE -ne 0)
{
    throw "Independent DataTable reload export failed with exit code $LASTEXITCODE"
}
& $VenvPython $VerifyScript `
    --reload-root $Reload `
    --fixture-report $FixtureReport `
    --client-report $ClientReport `
    --report $ReloadReport
if ($LASTEXITCODE -ne 0)
{
    throw "Independent DataTable reload verification failed with exit code $LASTEXITCODE"
}

$ChangedIds = @()
$Fixture = [System.IO.File]::ReadAllText($FixtureReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
foreach ($FixtureEntry in $Fixture.fixtures)
{
    $PackageFile = [string]$FixtureEntry.packageFilename
    $Id = [string]$FixtureEntry.id
    $HashAfter = (Get-FileHash -LiteralPath $PackageFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($HashAfter -ne $FixtureHashes[$Id])
    {
        $ChangedIds += $Id
    }
}
$UnexpectedChanged = @($ChangedIds | Where-Object { $_ -notin $SavedFixtureIds })
$MissingChanged = @($SavedFixtureIds | Where-Object { $_ -notin $ChangedIds })
if ($UnexpectedChanged.Count -gt 0 -or $MissingChanged.Count -gt 0)
{
    throw "Expected exactly $($SavedFixtureIds -join ', ') to change on disk; changed: $($ChangedIds -join ', ')"
}
if ((Get-FileHash -LiteralPath $Database -Algorithm SHA256).Hash.ToLowerInvariant() -ne $DatabaseHashBefore)
{
    throw "The immutable SQLite index changed during the DataTable Undo/Save test."
}
if ((Get-RevisionExportHash -Root $RevisionExport) -ne $RevisionExportHashBefore)
{
    throw "The frozen Revision Export changed during the DataTable Undo/Save test."
}

$ReloadValue = [System.IO.File]::ReadAllText($ReloadReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$ClientValue = [System.IO.File]::ReadAllText($ClientReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
Write-Utf8Json -Path $Summary -Value ([ordered]@{
    schemaVersion = "1.0"
    test = "mcp-live-data-table-undo-save"
    succeeded = $true
    engineRoot = $EngineRoot
    project = $ProjectPath
    editorPid = [int]$EditorProcess.Id
    output = $Output
    clientReport = $ClientReport
    reloadReport = $ReloadReport
    successCases = [int]$ClientValue.successCases
    undoVerified = [int]$ClientValue.undoVerified
    undoChangedAllTrue = [bool]$ClientValue.undoChangedAllTrue
    noOpVerified = [bool]$ClientValue.noOpVerified
    rejectedUndoIdentities = @($ClientValue.rejectedUndoIdentities)
    chainedWritesVerified = [bool]$ClientValue.chainedWritesVerified
    chainedUndoOrdered = [bool]$ClientValue.chainedUndoOrdered
    dryRunGatesVerified = [bool]$ClientValue.dryRunGatesVerified
    saves = $ClientValue.saves
    staleRevisionRejected = [bool]$ClientValue.staleRevisionRejected
    reloadVerified = [bool]$ReloadValue.verified
    changedPackages = @($ChangedIds)
    untouchedPackages = @($Fixture.fixtures | Where-Object { [string]$_.id -notin $SavedFixtureIds } | ForEach-Object { [string]$_.id })
    diskHashesBefore = $FixtureHashes
    databaseHashBefore = $DatabaseHashBefore
    databaseHashAfter = (Get-FileHash -LiteralPath $Database -Algorithm SHA256).Hash.ToLowerInvariant()
    revisionExportHashBefore = $RevisionExportHashBefore
    revisionExportHashAfter = (Get-RevisionExportHash -Root $RevisionExport)
    notes = @(
        "Authorized-saved packages: $($SavedFixtureIds -join ', '); fields and add stayed unsaved.",
        "Undo restored and re-applied all five families, including structural remove/rename.",
        "The reload export and replay verification ran in a separate UnrealEditor-Cmd process after the main Editor exited.",
        "Redo is still not exposed by any MCP Tool; see client-report.json knownDefects."
    )
})

Write-Host "MCP Live Editor DataTable Undo/Save/Reload test passed."
Write-Host "Output: $Output"
Write-Host "Editor PID: $($EditorProcess.Id)"
