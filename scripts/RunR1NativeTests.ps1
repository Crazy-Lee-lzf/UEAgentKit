param(
    [string]$EngineRoot = "E:\EPICGAME\UE_5.6",
    [switch]$SkipBuild,
    [string]$Filter = "UEAgentKit.Native.R1",
    [string]$Label = "",
    [string]$EvidenceGroup = "t4",
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
# The editor test host must be minimized, not hidden, and only our own PID may
# be touched.  No GUI interaction / synthetic input is performed.
if (-not ("UEAgentKitWindowMinimizer" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class UEAgentKitWindowMinimizer
{
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr hWnd, int command);
    public static int Minimize(uint processId)
    {
        int count = 0;
        EnumWindows((hWnd, lParam) =>
        {
            uint owner;
            GetWindowThreadProcessId(hWnd, out owner);
            if (owner == processId && ShowWindow(hWnd, 6))
            {
                count++;
            }
            return true;
        }, IntPtr.Zero);
        return count;
    }
}
"@
}

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

$Process = Start-Process -FilePath $EditorCmd -ArgumentList $ArgumentLine -WindowStyle Minimized -PassThru `
    -RedirectStandardOutput $LogPath -RedirectStandardError "$LogPath.err"
Start-Sleep -Milliseconds 400
$MinimizedWindows = [UEAgentKitWindowMinimizer]::Minimize([uint32]$Process.Id)
Write-Host "Launched minimized editor PID=$($Process.Id), minimized windows=$MinimizedWindows"
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

$ContextExportPath = Join-Path $HostDir "Saved\UEAgentKit\R1Native\context-export.json"
if (Test-Path -LiteralPath $ContextExportPath -PathType Leaf) {
    Copy-Item -LiteralPath $ContextExportPath -Destination (Join-Path $EvidenceDir "context-export.json") -Force
    Write-Host "Captured context export: $(Join-Path $EvidenceDir 'context-export.json')"
} else {
    Write-Warning "No context export was produced at $ContextExportPath"
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

if (-not [string]::IsNullOrWhiteSpace($Label)) {
    $LabelDir = Join-Path $EvidenceDir (Join-Path $EvidenceGroup $Label)
    New-Item -ItemType Directory -Force -Path $LabelDir | Out-Null
    foreach ($File in @(
        (Join-Path $EvidenceDir "catalog-export.json"),
        (Join-Path $EvidenceDir "context-export.json"),
        (Join-Path $EvidenceDir "environment-$Stamp.json"),
        $LogPath
    )) {
        if (Test-Path -LiteralPath $File -PathType Leaf) {
            Copy-Item -LiteralPath $File -Destination $LabelDir -Force
        }
    }
    $IndexPath = Join-Path $ReportDir "index.json"
    if (Test-Path -LiteralPath $IndexPath -PathType Leaf) {
        Copy-Item -LiteralPath $IndexPath -Destination (Join-Path $LabelDir "automation-index.json") -Force
    }
    Write-Host "Captured labeled evidence: $LabelDir"
}

if ($ExitCode -ne 0) {
    throw "Automation run failed with exit code $ExitCode (see $LogPath)"
}
