param(
    [string]$EngineRoot = "E:\EPICGAME\UE_5.6",
    [switch]$SkipBuild,
    [switch]$SkipManifest,
    [ValidateSet("all","shadow","off","manifest")] [string]$Only = "all"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EvidenceRoot = Join-Path $RepoRoot "Build\r1-native\Evidence\R15"
$HostDir = Join-Path $RepoRoot "Build\r1-native\R1AutomationHost"
$Project = Join-Path $HostDir "R1AutomationHost.uproject"
$Descriptor = Join-Path $HostDir "Saved\UEAgentKit\EditorBridge.json"
$WarmupFixture = Join-Path $HostDir "Saved\UEAgentKit\R1Native\socket-fixture.json"
$Fixture = "/Game/R1Native/BP_R1ContextFixture.BP_R1ContextFixture"
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
$Python = "py"

New-Item -ItemType Directory -Force -Path $EvidenceRoot | Out-Null

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
        $details = $busy | Select-Object ProcessId, Name, CommandLine | Format-List | Out-String
        throw "Heavy UE slot is busy; refusing to start another editor:`n$details"
    }
}

function Set-RegistryMode([string]$Mode) {
    $config = Join-Path $HostDir "Config\DefaultGame.ini"
    $text = [System.IO.File]::ReadAllText($config)
    if ($text -match '(?m)^NativeToolRegistry=') {
        $text = [regex]::Replace($text, '(?m)^NativeToolRegistry=.*$', "NativeToolRegistry=$Mode")
    } else {
        $text = $text.TrimEnd() + "`r`nNativeToolRegistry=$Mode`r`n"
    }
    [System.IO.File]::WriteAllText($config, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function Start-MinimizedEditor([string]$ProjectPath, [string]$Label, [string]$ExecCmds = "") {
    Assert-HeavySlotFree
    $labelDir = Join-Path $EvidenceRoot $Label
    New-Item -ItemType Directory -Force -Path $labelDir | Out-Null
    $log = Join-Path $labelDir "editor.log"
    $arguments = @(
        "`"$ProjectPath`"",
        "-unattended", "-nop4", "-nosplash", "-NullRHI", "-NoSound"
    )
    if ($ExecCmds) {
        $arguments += "-ExecCmds=`"$ExecCmds`""
    }
    $arguments += @("-log", "-stdout", "-FullStdOutLogOutput")
    $process = Start-Process -FilePath $EditorCmd -ArgumentList ($arguments -join " ") -WindowStyle Minimized -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    Start-Sleep -Milliseconds 400
    $minimized = [UEAgentKitWindowMinimizer]::Minimize([uint32]$process.Id)
    Write-Host "Started minimized editor PID=$($process.Id) minimizedWindows=$minimized label=$Label"
    return [pscustomobject]@{ Process = $process; Log = $log; LabelDir = $labelDir }
}

function Wait-Bridge([int]$ProcessId, [string]$DescriptorPath, [int]$TimeoutSeconds = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $DescriptorPath) {
            try {
                $descriptor = Get-Content -LiteralPath $DescriptorPath -Raw | ConvertFrom-Json
                if ([int]$descriptor.processId -eq $ProcessId) { return $descriptor }
            } catch { }
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Bridge descriptor for PID $ProcessId did not appear at $DescriptorPath"
}

function Wait-Fixture([string]$Path, [datetime]$NotBefore, [int]$TimeoutSeconds = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $Path) {
            $written = (Get-Item -LiteralPath $Path).LastWriteTimeUtc
            if ($written -ge $NotBefore.AddSeconds(-2)) { return }
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Socket warmup fixture did not appear at $Path"
}

function Stop-EditorProcess($Process) {
    if ($null -eq $Process) { return }
    try {
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force
            $Process.WaitForExit(15000) | Out-Null
        }
    } catch { }
}

function Run-Harness([string]$Mode, [string]$Label, [int]$ProcessId, [string]$ProjectDir, [string]$Out, [string]$DescriptorPath, [string]$FixtureInfo = "") {
    Push-Location $RepoRoot
    try {
        $harnessArgs = @(
            "-3.12", "tests\native_tools\r15_socket_harness.py",
            "--descriptor", $DescriptorPath, "--pid", $ProcessId, "--mode", $Mode, "--out", $Out,
            "--project-dir", $ProjectDir, "--asset-path", $Fixture, "--descriptor-timeout", "60"
        )
        if ($FixtureInfo -and $Mode -eq "shadow") {
            $harnessArgs += @("--fixture-info", $FixtureInfo)
        }
        & $Python @harnessArgs | Out-Host
        $code = $LASTEXITCODE
        return $code
    } finally {
        Pop-Location
    }
}

function Save-ProcessMetrics([int]$ProcessId, [string]$Path) {
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

function Run-OriginalHost([string]$Mode, [string]$Label) {
    Set-RegistryMode $Mode
    $launchUtc = (Get-Date).ToUniversalTime()
    $exec = ""
    if ($Mode -eq "shadow") {
        $exec = "Automation RunTests UEAgentKit.Native.SocketWarmup"
    }
    $editor = Start-MinimizedEditor $Project $Label $exec
    try {
        $bridgeDescriptor = Wait-Bridge $editor.Process.Id $Descriptor
        if ($Mode -eq "shadow") {
            Wait-Fixture $WarmupFixture $launchUtc
        }
        $out = Join-Path $editor.LabelDir "socket.json"
        $code = Run-Harness $Mode $Label $editor.Process.Id $HostDir $out $Descriptor $WarmupFixture
        Save-ProcessMetrics $editor.Process.Id (Join-Path $editor.LabelDir "process-metrics.json")
        [pscustomobject]@{
            mode = $Mode
            label = $Label
            editorPid = $editor.Process.Id
            harnessExit = $code
            port = $bridgeDescriptor.port
            descriptorGenerated = $bridgeDescriptor.startedUtc
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $editor.LabelDir "run-meta.json") -Encoding utf8NoBOM
        return $code
    } finally {
        Stop-EditorProcess $editor.Process
        Set-RegistryMode "off"
    }
}

function Copy-ManifestHost {
    $manifestHost = Join-Path $RepoRoot "Build\r15-manifest-host"
    $plugins = Join-Path $manifestHost "Plugins\UEAgentKit"
    if (-not (Test-Path -LiteralPath (Join-Path $manifestHost "R1AutomationHost.uproject"))) {
        New-Item -ItemType Directory -Force -Path $plugins | Out-Null
        Copy-Item -LiteralPath (Join-Path $HostDir "R1AutomationHost.uproject") -Destination $manifestHost
        Copy-Item -LiteralPath (Join-Path $HostDir "Source") -Destination $manifestHost -Recurse
        Copy-Item -LiteralPath (Join-Path $HostDir "Config") -Destination $manifestHost -Recurse
        foreach ($item in @("Source", "Resources", "Config", "UEAgentKit.uplugin")) {
            Copy-Item -LiteralPath (Join-Path $RepoRoot "Plugin\UEAgentKit\$item") -Destination $plugins -Recurse
        }
        if (-not (Test-Path -LiteralPath (Join-Path $plugins "Binaries"))) {
            New-Item -ItemType Directory -Force -Path (Join-Path $plugins "Binaries") | Out-Null
        }
    }
    return $manifestHost
}

function Set-ManifestVariant([string]$ManifestPath, [string]$Variant, [string]$SourceManifest) {
    $hold = "$ManifestPath.hold"
    if (Test-Path -LiteralPath $hold) { Move-Item -LiteralPath $hold -Destination $ManifestPath -Force }
    switch ($Variant) {
        "missing" {
            if (Test-Path -LiteralPath $ManifestPath) { Move-Item -LiteralPath $ManifestPath -Destination $hold -Force }
        }
        "corrupt" {
            "{ not json" | Set-Content -LiteralPath $ManifestPath -Encoding utf8NoBOM
        }
        "version" {
            $doc = Get-Content -LiteralPath $SourceManifest -Raw | ConvertFrom-Json
            $doc.schemaVersion = "ueak.native-registry/9.9"
            $doc | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $ManifestPath -Encoding utf8NoBOM
        }
        default {
            Copy-Item -LiteralPath $SourceManifest -Destination $ManifestPath -Force
        }
    }
}

function Run-ManifestHost([string]$Variant) {
    $manifestHost = Copy-ManifestHost
    $manifestProject = Join-Path $manifestHost "R1AutomationHost.uproject"
    $manifestDescriptor = Join-Path $manifestHost "Saved\UEAgentKit\EditorBridge.json"
    $manifestPath = Join-Path $manifestHost "Plugins\UEAgentKit\Resources\NativeTools\native-tools.v1.json"
    $sourceManifest = Join-Path $RepoRoot "Plugin\UEAgentKit\Resources\NativeTools\native-tools.v1.json"
    $manifestConfig = Join-Path $manifestHost "Config\DefaultGame.ini"
    $cfg = [System.IO.File]::ReadAllText($manifestConfig)
    $cfg = [regex]::Replace($cfg, '(?m)^NativeToolRegistry=.*$', "NativeToolRegistry=shadow")
    [System.IO.File]::WriteAllText($manifestConfig, $cfg, (New-Object System.Text.UTF8Encoding($false)))
    Set-ManifestVariant $manifestPath $Variant $sourceManifest

    $label = "manifest-$Variant"
    $editor = Start-MinimizedEditor $manifestProject $label
    try {
        $bridgeDescriptor = Wait-Bridge $editor.Process.Id $manifestDescriptor
        $out = Join-Path $editor.LabelDir "socket.json"
        Push-Location $RepoRoot
        try {
            & $Python -3.12 "tests\native_tools\r15_socket_harness.py" `
                --descriptor $manifestDescriptor --pid $editor.Process.Id --mode manifest --out $out `
                --project-dir $manifestHost --asset-path $Fixture | Out-Host
            $code = $LASTEXITCODE
        } finally { Pop-Location }
        Save-ProcessMetrics $editor.Process.Id (Join-Path $editor.LabelDir "process-metrics.json")
        [pscustomobject]@{
            mode = "manifest"; variant = $Variant; label = $label
            editorPid = $editor.Process.Id; harnessExit = $code
            port = $bridgeDescriptor.port; descriptorGenerated = $bridgeDescriptor.startedUtc
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $editor.LabelDir "run-meta.json") -Encoding utf8NoBOM
        return $code
    } finally {
        Stop-EditorProcess $editor.Process
    }
}

$failures = @()
if (-not $SkipBuild) {
    Assert-HeavySlotFree
    & (Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat") R1AutomationHostEditor Win64 Development `
        "-Project=$Project" -WaitMutex -NoHotReload
    if ($LASTEXITCODE -ne 0) { throw "R1 host build failed with exit code $LASTEXITCODE" }
}

if ($Only -eq "all" -or $Only -eq "shadow") {
    $shadowCode = Run-OriginalHost "shadow" "shadow"
    if ($shadowCode -ne 0) { $failures += "shadow" }
}
if ($Only -eq "all" -or $Only -eq "off") {
    $offCode = Run-OriginalHost "off" "off"
    if ($offCode -ne 0) { $failures += "off" }
}

if (-not $SkipManifest -and ($Only -eq "all" -or $Only -eq "manifest")) {
    $manifestHost = Copy-ManifestHost
    Assert-HeavySlotFree
    & (Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat") R1AutomationHostEditor Win64 Development `
        "-Project=$(Join-Path $manifestHost 'R1AutomationHost.uproject')" -WaitMutex -NoHotReload
    if ($LASTEXITCODE -ne 0) { throw "manifest host build failed with exit code $LASTEXITCODE" }
    foreach ($variant in @("missing", "corrupt", "version")) {
        $code = Run-ManifestHost $variant
        if ($code -ne 0) { $failures += "manifest-$variant" }
    }
}

$summary = [pscustomobject]@{
    schemaVersion = "ueak.r1.evidence-r15/1.0"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    failures = $failures
    ok = ($failures.Count -eq 0)
}
$summary | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $EvidenceRoot "r15-runs-summary.json") -Encoding utf8NoBOM
if ($failures.Count -ne 0) {
    throw "R1.5 socket acceptance failures: $($failures -join ', ')"
}
