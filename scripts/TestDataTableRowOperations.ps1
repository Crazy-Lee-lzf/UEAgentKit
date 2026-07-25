param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Output = ""
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")
$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$RunAssetCatalog = Join-Path $PSScriptRoot "RunAssetCatalog.ps1"
$RunPatch = Join-Path $PSScriptRoot "RunPatch.ps1"
$RunRollback = Join-Path $PSScriptRoot "RunRollback.ps1"
if ([string]::IsNullOrWhiteSpace($Output)) { $Output = Join-Path $ToolRoot "Output\DataTableRowOperations054" }
$Output = [System.IO.Path]::GetFullPath($Output)
$SafeRoot = [System.IO.Path]::GetFullPath((Join-Path $ToolRoot "Output"))
if (!$Output.StartsWith($SafeRoot.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe Output: $Output" }
if (Test-Path $Output) { Remove-Item $Output -Recurse -Force }
New-Item -ItemType Directory -Path $Output -Force | Out-Null

$AssetPackage = "/Game/UEAgentKitWriteTests/DT_CellPatchTarget"
$AssetPath = "$AssetPackage.DT_CellPatchTarget"
$AssetClass = "/Script/Engine.DataTable"
$RowStruct = "/Script/GameplayTags.GameplayTagTableRow"
$SourceRow = "UEAK_RowOps_Source"
$RenamedRow = "UEAK_RowOps_Renamed"
$ProjectDir = Split-Path -Parent $ProjectPath
$PackageFile = Join-Path $ProjectDir "Content\UEAgentKitWriteTests\DT_CellPatchTarget.uasset"
$Emergency = Join-Path $Output "baseline.uasset"
Copy-Item $PackageFile $Emergency -Force
$Restored = $false

function Write-Json([string]$Path, [object]$Value) {
    [IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 40) + "`r`n"), [Text.UTF8Encoding]::new($false))
}
function Export-One([string]$Name) {
    $Dir = Join-Path $Output $Name
    $Captured = & $RunAssetCatalog -EngineRoot $EngineRoot -ProjectPath $ProjectPath -Asset $AssetPackage -Output $Dir
    $Captured | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "Export $Name failed: $LASTEXITCODE" }
    $File = Get-ChildItem (Join-Path $Dir "canonical") -Filter *.json -File -Recurse | Select-Object -First 1
    return [pscustomobject]@{ Root=$Dir; Value=([IO.File]::ReadAllText($File.FullName,[Text.Encoding]::UTF8)|ConvertFrom-Json) }
}
function Row-Names($Canonical) { return @($Canonical.assetDetails.rows | ForEach-Object { [string]$_.Name } | Sort-Object) }
function Assert-RowState($Canonical, [bool]$HasSource, [bool]$HasRenamed) {
    $Names = Row-Names $Canonical
    if (($Names -contains $SourceRow) -ne $HasSource) { throw "Unexpected source-row state: $($Names -join ',')" }
    if (($Names -contains $RenamedRow) -ne $HasRenamed) { throw "Unexpected renamed-row state: $($Names -join ',')" }
}
function Make-Policy([string[]]$Ops) {
    return [ordered]@{
        schemaVersion="1.0"; validationEnabled=$true; commitEnabled=$true
        allowedProjectNames=@([IO.Path]::GetFileNameWithoutExtension($ProjectPath))
        allowedAssetRoots=@("/Game/UEAgentKitWriteTests")
        allowedReferenceRoots=@(); allowedReferenceClasses=@(); allowedOperations=$Ops
        allowedAssetClasses=@($AssetClass); allowedAssetProperties=@(); allowedMaterialParameters=@()
        allowedDataTableFields=@("$AssetClass#$RowStruct#Tag","$AssetClass#$RowStruct#DevComment")
        requireRevision=$true; rejectDirtyPackages=$true; maxAssetsPerPatch=1; maxOperationsPerAsset=1; maxValueBytes=4096
    }
}
function Invoke-Op([string]$Id,[string]$Operation,[hashtable]$Target,[object]$Value,[string]$ExpectedRevision,[string]$RevisionExport,[string]$Mode,[string]$Manifest="") {
    $PatchPath=Join-Path $Output "$Id.patch.json"; $PolicyPath=Join-Path $Output "$Id.policy.json"; $Report=Join-Path $Output "$Id.$($Mode.ToLower()).report.json"; $Validation=Join-Path $Output "$Id.$($Mode.ToLower()).validation.json"; $Backup=Join-Path $Output "Backups\$Id"
    Write-Json $PolicyPath (Make-Policy @($Operation))
    Write-Json $PatchPath ([ordered]@{schemaVersion="1.0";patchId=$Id;projectName=[IO.Path]::GetFileNameWithoutExtension($ProjectPath);description=$Id;assets=@([ordered]@{assetPath=$AssetPath;expectedRevision=$ExpectedRevision;expectedAssetClass=$AssetClass;operations=@([ordered]@{operationId=$Id;operation=$Operation;target=$Target;value=$Value})})})
    $Args=@{EngineRoot=$EngineRoot;ProjectPath=$ProjectPath;Patch=$PatchPath;Policy=$PolicyPath;RevisionExport=$RevisionExport;Mode=$Mode;Report=$Report;ValidationReport=$Validation;BackupDir=$Backup}
    if ($Manifest) { $Args.Manifest=$Manifest }
    $ProcessArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $RunPatch, "-EngineRoot", $EngineRoot, "-ProjectPath", $ProjectPath, "-Patch", $PatchPath, "-Policy", $PolicyPath, "-RevisionExport", $RevisionExport, "-Mode", $Mode, "-Report", $Report, "-ValidationReport", $Validation, "-BackupDir", $Backup)
    if ($Manifest) { $ProcessArgs += @("-Manifest", $Manifest) }
    $Process = Start-Process -FilePath "powershell.exe" -ArgumentList $ProcessArgs -NoNewWindow -Wait -PassThru
    if ($Process.ExitCode -ne 0) { throw "$Operation $Mode failed: $($Process.ExitCode)" }
    return [IO.File]::ReadAllText($Report,[Text.Encoding]::UTF8)|ConvertFrom-Json
}
function Invoke-Rollback([string]$Manifest,[string]$PolicyPath,[string]$BackupRoot,[string]$Name) {
    $Report=Join-Path $Output "$Name.rollback.json"; $VerifyRoot=Join-Path $Output "$Name.verify"; $VerifyReport=Join-Path $Output "$Name.verify.json"
    $ProcessArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $RunRollback, "-EngineRoot", $EngineRoot, "-ProjectPath", $ProjectPath, "-Manifest", $Manifest, "-Policy", $PolicyPath, "-BackupRoot", $BackupRoot, "-Mode", "Commit", "-Report", $Report, "-VerificationOutput", $VerifyRoot, "-VerificationReport", $VerifyReport)
    $Process = Start-Process -FilePath "powershell.exe" -ArgumentList $ProcessArgs -NoNewWindow -Wait -PassThru
    if ($Process.ExitCode -ne 0) { throw "Rollback $Name failed: $($Process.ExitCode)" }
    $R=[IO.File]::ReadAllText($Report,[Text.Encoding]::UTF8)|ConvertFrom-Json
    $V=[IO.File]::ReadAllText($VerifyReport,[Text.Encoding]::UTF8)|ConvertFrom-Json
    if (!$R.restored -or !$V.verified) { throw "Rollback $Name verification failed" }
}
try {
    $Initial=Export-One "Initial"; $InitialRev=[string]$Initial.Value.revision.value
    Assert-RowState $Initial.Value $false $false

    $Dry=Invoke-Op "add-row-dry" "addDataTableRow" ([ordered]@{rowName=$SourceRow}) ([ordered]@{Tag="UEAgentKit.RowOps";DevComment="Added"}) $InitialRev $Initial.Root "DryRun"
    if (!$Dry.rolledBack -or !$Dry.rollbackStructureMatch -or !$Dry.diskUnchanged) { throw "Add Dry Run atomicity gates failed" }
    $AfterDry=Export-One "AfterAddDry"; Assert-RowState $AfterDry.Value $false $false
    if ([string]$AfterDry.Value.revision.value -ne $InitialRev) { throw "Add Dry Run changed revision" }

    $AddManifest=Join-Path $Output "Backups\add\add.manifest.json"; $AddPolicy=Join-Path $Output "add.policy.json"; Write-Json $AddPolicy (Make-Policy @("addDataTableRow"))
    $Add=Invoke-Op "add" "addDataTableRow" ([ordered]@{rowName=$SourceRow}) ([ordered]@{Tag="UEAgentKit.RowOps";DevComment="Added"}) $InitialRev $Initial.Root "Commit" $AddManifest
    $AfterAdd=Export-One "AfterAdd"; Assert-RowState $AfterAdd.Value $true $false

    $RenameManifest=Join-Path $Output "Backups\rename\rename.manifest.json"; $RenamePolicy=Join-Path $Output "rename.policy.json"; Write-Json $RenamePolicy (Make-Policy @("renameDataTableRow"))
    $Rename=Invoke-Op "rename" "renameDataTableRow" ([ordered]@{rowName=$SourceRow;newRowName=$RenamedRow}) $true ([string]$AfterAdd.Value.revision.value) $AfterAdd.Root "Commit" $RenameManifest
    $AfterRename=Export-One "AfterRename"; Assert-RowState $AfterRename.Value $false $true

    $RemoveManifest=Join-Path $Output "Backups\remove\remove.manifest.json"; $RemovePolicy=Join-Path $Output "remove.policy.json"; Write-Json $RemovePolicy (Make-Policy @("removeDataTableRow"))
    $Remove=Invoke-Op "remove" "removeDataTableRow" ([ordered]@{rowName=$RenamedRow}) $true ([string]$AfterRename.Value.revision.value) $AfterRename.Root "Commit" $RemoveManifest
    $AfterRemove=Export-One "AfterRemove"; Assert-RowState $AfterRemove.Value $false $false

    Invoke-Rollback $RemoveManifest $RemovePolicy (Split-Path -Parent $RemoveManifest) "remove"
    $R1=Export-One "RollbackRemove"; Assert-RowState $R1.Value $false $true
    Invoke-Rollback $RenameManifest $RenamePolicy (Split-Path -Parent $RenameManifest) "rename"
    $R2=Export-One "RollbackRename"; Assert-RowState $R2.Value $true $false
    Invoke-Rollback $AddManifest $AddPolicy (Split-Path -Parent $AddManifest) "add"
    $R3=Export-One "RollbackAdd"; Assert-RowState $R3.Value $false $false
    if ([string]$R3.Value.revision.value -ne $InitialRev) { throw "Final revision mismatch" }
    $Restored=$true
}
finally {
    if (!$Restored -and @(Get-Process UnrealEditor,UnrealEditor-Cmd -ErrorAction SilentlyContinue).Count -eq 0) { Copy-Item $Emergency $PackageFile -Force; Write-Warning "Emergency package restored" }
}
Write-Host "DataTable row operations regression passed."
Write-Host "Initial Revision : $InitialRev"
Write-Host "Restored Revision: $($R3.Value.revision.value)"
