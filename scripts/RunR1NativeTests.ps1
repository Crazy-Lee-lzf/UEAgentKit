param(
    [string]$EngineRoot = "E:\EPICGAME\UE_5.6",
    [switch]$SkipBuild,
    [string]$Filter = "UEAgentKit.Native.R1",
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$HostDir = Join-Path $RepoRoot "Build\r1-native\R1AutomationHost"
$Project = Join-Path $HostDir "R1AutomationHost.uproject"
$EvidenceDir = Join-Path $RepoRoot "Build\r1-native\Evidence"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReportDir = Join-Path $EvidenceDir "AutomationReport-$Stamp"
$LogPath = Join-Path $EvidenceDir "automation-r1-native-$Stamp.log"

New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

if (-not (Test-Path -LiteralPath $Project -PathType Leaf)) {
    throw "Disposable host project is missing: $Project"
}
if (-not (Test-Path -LiteralPath (Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe") -PathType Leaf)) {
    throw "UnrealEditor-Cmd.exe not found under $EngineRoot"
}

if (-not $SkipBuild) {
    & (Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat") R1AutomationHostEditor Win64 Development "-Project=$Project" -WaitMutex -NoHotReload
    if ($LASTEXITCODE -ne 0) {
        throw "UEAgentKitEditor build failed with exit code $LASTEXITCODE"
    }
}

$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
# One pre-quoted command line: UE only parses -ExecCmds / -TestExit correctly when
# the value (which contains spaces) reaches the process quoted.
$ArgumentLine = @(
    "`"$Project`"",
    "-unattended",
    "-nop4",
    "-nosplash",
    "-NullRHI",
    "-NoSound",
    "-ExecCmds=`"Automation RunTests $Filter`"",
    "-TestExit=`"Automation Test Queue Empty`"",
    "-ReportExportPath=`"$ReportDir`"",
    "-log",
    "-stdout",
    "-FullStdOutLogOutput"
) -join " "

$Process = Start-Process -FilePath $EditorCmd -ArgumentList $ArgumentLine -NoNewWindow -PassThru `
    -RedirectStandardOutput $LogPath -RedirectStandardError "$LogPath.err"
$Finished = $Process.WaitForExit($TimeoutSeconds * 1000)
if (-not $Finished) {
    $Process.Kill()
    throw "Automation run exceeded $TimeoutSeconds seconds and was terminated"
}

$ExitCode = $Process.ExitCode
Write-Host "UnrealEditor-Cmd exit code: $ExitCode"

$ExportPath = Join-Path $HostDir "Saved\UEAgentKit\R1Native\catalog-export.json"
if (Test-Path -LiteralPath $ExportPath -PathType Leaf) {
    Copy-Item -LiteralPath $ExportPath -Destination (Join-Path $EvidenceDir "catalog-export.json") -Force
    Write-Host "Captured catalog export: $(Join-Path $EvidenceDir 'catalog-export.json')"
} else {
    Write-Warning "No catalog export was produced at $ExportPath"
}

$PluginDll = Join-Path $HostDir "Plugins\UEAgentKit\Binaries\Win64\UnrealEditor-UEAgentKitEditor.dll"
$DllHash = ""
if (Test-Path -LiteralPath $PluginDll -PathType Leaf) {
    $DllHash = (Get-FileHash -LiteralPath $PluginDll -Algorithm SHA256).Hash.ToLower()
}

$Environment = [ordered]@{
    schemaVersion = "ueak.r1.evidence/1.0"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    engineRoot = $EngineRoot
    engineBuildVersion = (Get-Content -LiteralPath (Join-Path $EngineRoot "Engine\Build\Build.version") -Raw | ConvertFrom-Json)
    project = $Project
    automationFilter = $Filter
    reportDirectory = $ReportDir
    logPath = $LogPath
    exitCode = $ExitCode
    pluginBuildHash = if ($DllHash) { "sha256:$DllHash" } else { "" }
}
$Environment | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $EvidenceDir "environment-$Stamp.json") -Encoding utf8NoBOM

if ($ExitCode -ne 0) {
    throw "Automation run failed with exit code $ExitCode (see $LogPath)"
}
