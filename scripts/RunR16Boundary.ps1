param(
    [string]$EngineRoot = "E:\EPICGAME\UE_5.6",
    [switch]$SkipBuild,
    [int]$PerfRuns = 3
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Evidence = Join-Path $RepoRoot "Build\r1-native\Evidence\R16"
$HostDir = Join-Path $RepoRoot "Build\r1-native\R1AutomationHost"
$Project = Join-Path $HostDir "R1AutomationHost.uproject"
$Descriptor = Join-Path $HostDir "Saved\UEAgentKit\EditorBridge.json"
$Fixture = Join-Path $HostDir "Saved\UEAgentKit\R1Native\socket-fixture.json"
$CursorFile = Join-Path $RepoRoot "Build\r1-native\R16-tmp\epoch-cursor.txt"
$BaselineCatalog = Join-Path $Evidence "r1-regression\catalog-export.json"
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
$TestKey = -join (1..32 | ForEach-Object { "{0:x2}" -f (Get-Random -Maximum 256) })
New-Item -ItemType Directory -Force -Path $Evidence | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $CursorFile) | Out-Null

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
            if (owner == processId && ShowWindow(hWnd, 6)) count++;
            return true;
        }, IntPtr.Zero);
        return count;
    }
}
"@
}

function Assert-HeavySlotFree {
    $busy = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match 'UnrealEditor|UnrealBuildTool|Uba|cl\.exe|link\.exe'
    }
    if ($busy) {
        throw "Heavy UE slot busy: $($busy | Select-Object ProcessId,Name | Format-Table | Out-String)"
    }
}

function Set-Mode([string]$Mode) {
    $config = Join-Path $HostDir "Config\DefaultGame.ini"
    $text = [System.IO.File]::ReadAllText($config)
    $text = [regex]::Replace($text, '(?m)^NativeToolRegistry=.*$', "NativeToolRegistry=$Mode")
    [System.IO.File]::WriteAllText($config, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function Start-Editor([string]$Label, [string[]]$Flags = @(), [hashtable]$Env = @{}, [string]$ExecCmds = "") {
    Assert-HeavySlotFree
    $dir = Join-Path $Evidence $Label
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $log = Join-Path $dir "editor.log"
    $args = @("`"$Project`"", "-unattended", "-nop4", "-nosplash", "-NullRHI", "-NoSound")
    if ($ExecCmds) { $args += "-ExecCmds=`"$ExecCmds`"" }
    $args += $Flags
    $args += @("-log", "-stdout", "-FullStdOutLogOutput")
    $saved = @{}
    foreach ($key in $Env.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, [string]$Env[$key], "Process")
    }
    try {
        $process = Start-Process -FilePath $EditorCmd -ArgumentList ($args -join " ") -WindowStyle Minimized -PassThru `
            -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    } finally {
        foreach ($key in $Env.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process")
        }
    }
    Start-Sleep -Milliseconds 400
    $minimized = [UEAgentKitWindowMinimizer]::Minimize([uint32]$process.Id)
    Write-Host "Started minimized editor PID=$($process.Id) minimized=$minimized label=$Label flags=$($Flags -join ' ')"
    return [pscustomobject]@{ Process = $process; Dir = $dir; Log = $log }
}

function Wait-Bridge([int]$ProcessId, [int]$Timeout = 180) {
    $deadline = (Get-Date).AddSeconds($Timeout)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $Descriptor) {
            try {
                $d = Get-Content $Descriptor -Raw | ConvertFrom-Json
                if ([int]$d.processId -eq $ProcessId) { return $d }
            } catch { }
        }
        Start-Sleep -Milliseconds 250
    }
    throw "No descriptor for PID $ProcessId"
}

function Wait-Fixture([int]$Timeout = 180) {
    $deadline = (Get-Date).AddSeconds($Timeout)
    $start = (Get-Date).ToUniversalTime().AddSeconds(-5)
    while ((Get-Date) -lt $deadline) {
        if ((Test-Path $Fixture) -and ((Get-Item $Fixture).LastWriteTimeUtc -ge $start)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "Warmup fixture did not refresh"
}

function Stop-Editor($Process) {
    if ($null -eq $Process) { return }
    try {
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force
            $Process.WaitForExit(15000) | Out-Null
        }
    } catch { }
}

function Save-Metrics([int]$ProcessId, [string]$Path) {
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        [pscustomobject]@{
            processId = $ProcessId
            workingSetBytes = $p.WorkingSet64
            peakWorkingSetBytes = $p.PeakWorkingSet64
            privateMemoryBytes = $p.PrivateMemorySize64
            sampledAtUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        } | ConvertTo-Json | Set-Content -LiteralPath $Path -Encoding utf8NoBOM
    } catch {
        "{}" | Set-Content -LiteralPath $Path -Encoding utf8NoBOM
    }
}

function Invoke-Harness([string]$Mode, [string]$Dir, [int]$ProcessId, [string[]]$Extra = @()) {
    if ($Mode -eq "shadow" -and (Test-Path $BaselineCatalog)) {
        $Extra += @("--baseline-catalog", $BaselineCatalog)
    }
    $out = Join-Path $Dir "socket.json"
    $args = @(
        "-3.12", "tests\native_tools\r15_socket_harness.py",
        "--descriptor", $Descriptor, "--pid", $ProcessId, "--mode", $Mode,
        "--out", $out, "--project-dir", $HostDir,
        "--asset-path", "/Game/R1Native/BP_R1ContextFixture.BP_R1ContextFixture",
        "--descriptor-timeout", "60"
    ) + $Extra
    Push-Location $RepoRoot
    try {
        & py @args | Out-Host
        return $LASTEXITCODE
    } finally { Pop-Location }
}

function Run-EditorCase([string]$Label, [string]$Mode, [string[]]$Flags = @(), [hashtable]$Env = @{}, [string]$ExecCmds = "", [string[]]$Extra = @()) {
    Set-Mode "shadow"
    $editor = Start-Editor $Label $Flags $Env $ExecCmds
    try {
        $d = Wait-Bridge $editor.Process.Id
        if ($ExecCmds) { Wait-Fixture }
        Save-Metrics $editor.Process.Id (Join-Path $editor.Dir "metrics-before.json")
        $code = Invoke-Harness $Mode $editor.Dir $editor.Process.Id $Extra
        Save-Metrics $editor.Process.Id (Join-Path $editor.Dir "metrics-after.json")
        [pscustomobject]@{
            label = $Label; mode = $Mode; pid = $editor.Process.Id; exit = $code
            port = $d.port; startedUtc = $d.startedUtc; flags = $Flags
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $editor.Dir "run-meta.json") -Encoding utf8NoBOM
        return $code
    } finally { Stop-Editor $editor.Process }
}

function Run-ShutdownCase {
    Set-Mode "shadow"
    $editor = Start-Editor "shutdown"
    try {
        $d = Wait-Bridge $editor.Process.Id
        $ready = Join-Path $editor.Dir ("shutdown-ready-" + (Get-Date -Format "HHmmssfff") + ".txt")
        $out = Join-Path $editor.Dir "socket.json"
        $pyArgs = @(
            "-3.12", "tests\native_tools\r15_socket_harness.py",
            "--descriptor", $Descriptor, "--pid", $editor.Process.Id, "--mode", "shutdown",
            "--out", $out, "--shutdown-ready", $ready, "--project-dir", $HostDir,
            "--descriptor-timeout", "60"
        )
        $stdout = Join-Path $editor.Dir "shutdown-harness.log"
        $proc = Start-Process -FilePath "py" -ArgumentList ($pyArgs -join " ") -PassThru -NoNewWindow `
            -RedirectStandardOutput $stdout -RedirectStandardError "$stdout.err"
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline -and -not (Test-Path $ready)) { Start-Sleep -Milliseconds 200 }
        if (-not (Test-Path $ready)) { throw "Shutdown harness did not become ready" }
        Stop-Editor $editor.Process
        $finished = $proc.WaitForExit(30000)
        if (-not $finished) { $proc.Kill(); throw "Shutdown harness did not exit after editor stop" }
        [pscustomobject]@{ label="shutdown"; pid=$proc.Id; exit=$proc.ExitCode; port=$d.port } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $editor.Dir "run-meta.json") -Encoding utf8NoBOM
        return $proc.ExitCode
    } finally { Stop-Editor $editor.Process }
}

$failures = @()
if (-not $SkipBuild) {
    Assert-HeavySlotFree
    & (Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat") R1AutomationHostEditor Win64 Development `
        "-Project=$Project" -WaitMutex -NoHotReload
    if ($LASTEXITCODE -ne 0) { throw "build failed: $LASTEXITCODE" }
}

# T9: three independent cold/warm shadow runs.
for ($i = 1; $i -le $PerfRuns; $i++) {
    $label = "perf-$i"
    $code = Run-EditorCase $label "shadow" @() @{} "Automation RunTests UEAgentKit.Native.SocketWarmup"
    if ($code -ne 0) { $failures += $label }
}

$seamFlags = @("-UEAgentKitSocketTest")
$keyEnv = @{ UEAK_TEST_CURSOR_KEY = $TestKey; UEAK_TEST_CURSOR_TTL = "120" }
$code = Run-EditorCase "capture" "capture" $seamFlags $keyEnv "" @("--cursor-out", $CursorFile)
if ($code -ne 0) { $failures += "capture" }

$code = Run-EditorCase "stale" "stale" $seamFlags $keyEnv "" @("--cursor-in", $CursorFile)
if ($code -ne 0) { $failures += "stale" }

$expiryEnv = @{ UEAK_TEST_CURSOR_KEY = $TestKey; UEAK_TEST_CURSOR_TTL = "2" }
$code = Run-EditorCase "expiry" "expiry" $seamFlags $expiryEnv "" @("--expiry-wait", "3")
if ($code -ne 0) { $failures += "expiry" }

$code = Run-ShutdownCase
if ($code -ne 0) { $failures += "shutdown" }

# Off gating on the final binary.
Set-Mode "off"
$offEditor = Start-Editor "off"
try {
    $d = Wait-Bridge $offEditor.Process.Id
    $code = Invoke-Harness "off" $offEditor.Dir $offEditor.Process.Id
    Save-Metrics $offEditor.Process.Id (Join-Path $offEditor.Dir "metrics-after.json")
    [pscustomobject]@{ label="off"; pid=$offEditor.Process.Id; exit=$code; port=$d.port } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $offEditor.Dir "run-meta.json") -Encoding utf8NoBOM
    if ($code -ne 0) { $failures += "off" }
} finally { Stop-Editor $offEditor.Process; Set-Mode "off" }

[pscustomobject]@{ failures = $failures; ok = ($failures.Count -eq 0) } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Evidence "r16-runs-summary.json") -Encoding utf8NoBOM
if ($failures.Count -ne 0) { throw "R1.6 failures: $($failures -join ', ')" }
