<#
.SYNOPSIS
    P5 real-Agent Blueprint end-to-end acceptance (MCP -> Socket -> UE 5.6).

.DESCRIPTION
    Runs the real product path, not a fixture-side C++ call:

      MCP stdio client -> ue_agent_kit.mcp_server -> Editor Bridge loopback socket
      -> UEAgentKitEditor EditorBridge -> Preview/Apply/Save -> real Blueprint asset

    The disposable HostProject gets one ordinary Actor Blueprint fixture with a
    default BeginPlay event graph.  A schema-current empty index database is
    created so the live MCP server can start without touching the production
    index.  Two Editor sessions are used:

      1. gate OFF: the experimental tools must refuse the live request.
      2. gate ON : GraphEdit -> PinDefaults -> VariableCreate -> Rewire ->
                   NodeRemove, each Preview/Apply/Save through MCP.

    A brand-new UnrealEditor-Cmd process then runs the read-only
    BlueprintAgentE2EReload commandlet to verify the saved bytes, exact nodes,
    pins, defaults, edges, variables and the deleted node GUID.

    The production project, production index and every other worktree are never
    touched.  The experimental ini gate is only written inside Build/DirectHost
    and is removed again at the end.

.EXAMPLE
    .\scripts\VerifyBlueprintAgentEndToEnd.ps1 -EngineRoot E:\EPICGAME\UE_5.6
#>
param(
    [string]$EngineRoot = "",
    [switch]$SkipBuild,
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "Common.ps1")

$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$HostDir = Join-Path $Root "Build\DirectHost"
$Project = Join-Path $HostDir "HostProject.uproject"
$EditorCmd = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
$ConfigDir = Join-Path $HostDir "Config"
$DefaultEngineIni = Join-Path $ConfigDir "DefaultEngine.ini"
$Descriptor = Join-Path $HostDir "Saved\UEAgentKit\EditorBridge.json"
$ExpectedJson = Join-Path $HostDir "Saved\UEAgentKitAgentE2E\expected.json"
$ReloadJson = Join-Path $HostDir "Saved\UEAgentKitAgentE2E\reload.json"
$ExportDir = Join-Path $HostDir "Saved\UEAgentKitAgentE2E\Export"
$Database = Join-Path $Root "Output\p5\agent-e2e\ueak-index.sqlite3"
$ContentDir = Join-Path $HostDir "Content\UEAgentKitAgentE2E"

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $Root ("Output\p5\agent-e2e\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$Output = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path $Output -Force | Out-Null

Assert-UeakPath -Path $EditorCmd -Description "UnrealEditor-Cmd.exe" -PathType File
Assert-UeakPath -Path $Project -Description "disposable HostProject" -PathType File

function Write-GateOffConfig
{
    if (Test-Path -LiteralPath $DefaultEngineIni)
    {
        Remove-Item -LiteralPath $DefaultEngineIni -Force
    }
}

function Write-GateOnConfig
{
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
    @"
[UEAgentKit]
ExperimentalGraphPatchEdit=True
"@ | Set-Content -LiteralPath $DefaultEngineIni -Encoding ascii
}

function Stop-EditorProcess
{
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process -or $Process.HasExited)
    {
        return
    }
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    if (-not $Process.WaitForExit(15000))
    {
        & taskkill.exe /PID $Process.Id /T /F 2>&1 | Out-Null
        $Process.WaitForExit(5000) | Out-Null
    }
    if (Test-Path -LiteralPath $Descriptor)
    {
        Remove-Item -LiteralPath $Descriptor -Force -ErrorAction SilentlyContinue
    }
}

function Start-FixtureEditor
{
    param([string]$LogPath)
    # A descriptor from a previous Editor session must never be mistaken for the
    # bridge of the Editor this call is about to launch.
    if (Test-Path -LiteralPath $Descriptor)
    {
        Remove-Item -LiteralPath $Descriptor -Force
    }
    $arguments = @(
        "`"$Project`"",
        "-nop4",
        "-nosplash",
        "-NoSound",
        "-stdout",
        "-FullStdOutLogOutput"
    )
    return Start-Process -FilePath $EditorCmd -ArgumentList $arguments -PassThru `
        -RedirectStandardOutput $LogPath -RedirectStandardError "$LogPath.err" `
        -WindowStyle Hidden
}

function Wait-EditorBridge
{
    param([int]$TimeoutSeconds = 300)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline)
    {
        if (Test-Path -LiteralPath $Descriptor)
        {
            try
            {
                $payload = Get-Content -LiteralPath $Descriptor -Raw | ConvertFrom-Json
                if ($payload.port -and $payload.authToken)
                {
                    return $payload
                }
            }
            catch
            {
                # descriptor can be observed mid-write; retry
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "The Editor Bridge descriptor did not appear within $TimeoutSeconds seconds: $Descriptor"
}

$Summary = [ordered]@{
    schemaVersion = "ueak.blueprint-agent-e2e-acceptance/1.0"
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    project = $Project
    stages = [ordered]@{
        build = "pending"
        fixture = "pending"
        index = "pending"
        gateOff = [ordered]@{ ok = $false; errorCode = ""; errorMessage = "" }
        crossProject = [ordered]@{ ok = $false; errorCode = ""; errorMessage = "" }
        fullSequence = [ordered]@{ ok = $false; nodeCount = 0; edgeCount = 0; variables = @(); assetSha256 = ""; planIds = @{} }
        reload = [ordered]@{ ok = $false; exitCode = -1; verified = $false; nodeCount = 0; edgeCount = 0; variableCount = 0; mismatches = @() }
    }
    ok = $false
}
$Failures = New-Object System.Collections.Generic.List[string]

try
{
    if (-not $SkipBuild)
    {
        Write-Host "Building plugin..."
        & (Join-Path $PSScriptRoot "BuildPluginDirect.ps1") -EngineRoot $EngineRoot *>&1 |
            Tee-Object -FilePath (Join-Path $Output "build.log") | Out-Null
        if ($LASTEXITCODE -ne 0)
        {
            throw "BuildPluginDirect failed with exit code $LASTEXITCODE."
        }
        $Summary.stages.build = "ok"
    }
    else
    {
        $Summary.stages.build = "skipped"
    }

    # Fresh disposable fixture state only.  Nothing outside Build/DirectHost.
    foreach ($path in @($ContentDir, (Join-Path $HostDir "Saved\UEAgentKitAgentE2E"), $ExportDir))
    {
        if (Test-Path -LiteralPath $path)
        {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
    if (Test-Path -LiteralPath $Descriptor)
    {
        Remove-Item -LiteralPath $Descriptor -Force
    }
    New-Item -ItemType Directory -Path $Output -Force | Out-Null

    Write-GateOffConfig

    # 1) Disposable fixture Blueprint (default event graph), saved on disk.
    $fixtureLog = Join-Path $Output "fixture-commandlet.log"
    & $EditorCmd $Project "-run=BlueprintWriteFixture" "Root=/Game/UEAgentKitAgentE2E" `
        -unattended -nop4 -nosplash -NullRHI -NoSound -stdout -FullStdOutLogOutput *>&1 |
        Tee-Object -FilePath $fixtureLog | Out-Null
    if ($LASTEXITCODE -ne 0)
    {
        throw "BlueprintWriteFixture commandlet failed with exit code $LASTEXITCODE."
    }
    Assert-UeakPath -Path (Join-Path $ContentDir "BP_AgentE2E.uasset") `
        -Description "disposable Agent E2E Blueprint" -PathType File
    $Summary.stages.fixture = "ok"

    # 2) Disposable schema-current index database (never the production index).
    $indexLog = Join-Path $Output "index-build.log"
    & py -3.12 (Join-Path $Root "scripts\New-DisposableAgentIndex.py") --database $Database *>&1 |
        Tee-Object -FilePath $indexLog | Out-Null
    if ($LASTEXITCODE -ne 0)
    {
        throw "Disposable index creation failed with exit code $LASTEXITCODE."
    }
    Assert-UeakPath -Path $Database -Description "disposable index database" -PathType File
    $Summary.stages.index = "ok"
    $probe = Join-Path $Root "tests\python\p5_agent_e2e_probe.py"

    # 3) Gate OFF negative through the real MCP + socket path.
    $offLog = Join-Path $Output "editor-gate-off.log"
    $offProcess = Start-FixtureEditor -LogPath $offLog
    try
    {
        Wait-EditorBridge | Out-Null
        $offOut = Join-Path $Output "gate-off"
        & py -3.12 $probe --project $Project --database $Database --out $offOut --mode gate-off *>&1 |
            Tee-Object -FilePath (Join-Path $Output "gate-off-probe.log") | Out-Null
        $offExit = $LASTEXITCODE
        $offReport = Join-Path $offOut "probe-report.json"
        Assert-UeakPath -Path $offReport -Description "gate-off probe report" -PathType File
        $offPayload = Get-Content -LiteralPath $offReport -Raw | ConvertFrom-Json
        if ($offExit -ne 0 -or -not [bool]$offPayload.ok)
        {
            throw "Gate-off MCP negative did not pass: $($offPayload.error | ConvertTo-Json -Compress)"
        }
        $Summary.stages.gateOff = [ordered]@{
            ok = $true
            errorCode = $offPayload.gateOffError.code
            errorMessage = $offPayload.gateOffError.message
        }
    }
    finally
    {
        Stop-EditorProcess -Process $offProcess
    }

    # 4) Gate ON positive: full MCP sequence.
    Write-GateOnConfig
    $onLog = Join-Path $Output "editor-gate-on.log"
    $onProcess = Start-FixtureEditor -LogPath $onLog
    try
    {
        Wait-EditorBridge | Out-Null
        $onOut = Join-Path $Output "full"
        & py -3.12 $probe --project $Project --database $Database --out $onOut --mode full *>&1 |
            Tee-Object -FilePath (Join-Path $Output "full-probe.log") | Out-Null
        $onExit = $LASTEXITCODE
        $onReport = Join-Path $onOut "probe-report.json"
        Assert-UeakPath -Path $onReport -Description "full probe report" -PathType File
        $onPayload = Get-Content -LiteralPath $onReport -Raw | ConvertFrom-Json
        if ($onExit -ne 0 -or -not [bool]$onPayload.ok)
        {
            throw "Full MCP E2E did not pass: $($onPayload.error | ConvertTo-Json -Compress)"
        }
        Assert-UeakPath -Path $ExpectedJson -Description "expected state JSON" -PathType File
        $Summary.stages.fullSequence = [ordered]@{
            ok = $true
            report = $onReport
            nodeCount = $onPayload.finalNodeCount
            edgeCount = $onPayload.finalEdgeCount
            variables = @($onPayload.variables)
            assetSha256 = $onPayload.finalAssetSha256
            planIds = $onPayload.planIds
        }

        # 4b) Cross-project guard: a server configured for another project must
        # not be able to address the running HostProject editor.
        $fakeRoot = Join-Path $Output "CrossProject"
        $fakeSaved = Join-Path $fakeRoot "Saved\UEAgentKit"
        New-Item -ItemType Directory -Path $fakeSaved -Force | Out-Null
        $fakeProject = Join-Path $fakeRoot "CrossProjectHost.uproject"
        Copy-Item -LiteralPath $Project -Destination $fakeProject -Force
        # A copied descriptor makes the server reach the project identity checks
        # instead of failing earlier because no bridge descriptor exists there.
        Copy-Item -LiteralPath $Descriptor -Destination (Join-Path $fakeSaved "EditorBridge.json") -Force
        $crossOut = Join-Path $Output "cross-project"
        & py -3.12 $probe --project $fakeProject --wrong-project $fakeProject `
            --database $Database --out $crossOut --mode cross-project *>&1 |
            Tee-Object -FilePath (Join-Path $Output "cross-project-probe.log") | Out-Null
        $crossExit = $LASTEXITCODE
        $crossReport = Join-Path $crossOut "probe-report.json"
        Assert-UeakPath -Path $crossReport -Description "cross-project probe report" -PathType File
        $crossPayload = Get-Content -LiteralPath $crossReport -Raw | ConvertFrom-Json
        if ($crossExit -ne 0 -or -not [bool]$crossPayload.ok)
        {
            throw "Cross-project MCP guard did not pass: $($crossPayload.error | ConvertTo-Json -Compress)"
        }
        $Summary.stages.crossProject = [ordered]@{
            ok = $true
            errorCode = $crossPayload.crossProjectError.code
            errorMessage = $crossPayload.crossProjectError.message
        }
    }
    finally
    {
        Stop-EditorProcess -Process $onProcess
    }

    # 5) Brand-new process: read-only disk reload verification.
    $reloadLog = Join-Path $Output "editor-reload.log"
    & $EditorCmd $Project "-run=BlueprintAgentE2EReload" -unattended -nop4 -nosplash `
        -NullRHI -NoSound -stdout -FullStdOutLogOutput *>&1 |
        Tee-Object -FilePath $reloadLog | Out-Null
    $reloadExit = $LASTEXITCODE
    Assert-UeakPath -Path $ReloadJson -Description "reload result" -PathType File
    $reloadPayload = Get-Content -LiteralPath $ReloadJson -Raw | ConvertFrom-Json
    $reloadOk = ($reloadExit -eq 0) -and [bool]$reloadPayload.verified -and `
        [bool]$reloadPayload.assetSha256Verified -and (@($reloadPayload.mismatches).Count -eq 0)
    $Summary.stages.reload = [ordered]@{
        ok = [bool]$reloadOk
        exitCode = [int]$reloadExit
        path = $ReloadJson
        verified = $reloadPayload.verified
        nodeCount = $reloadPayload.nodeCount
        edgeCount = $reloadPayload.edgeCount
        variableCount = $reloadPayload.variableCount
        mismatches = @($reloadPayload.mismatches)
    }
    if (-not $reloadOk)
    {
        throw "Fresh-process reload verification failed: $($reloadPayload.mismatches | ConvertTo-Json -Compress)"
    }

    $Summary.ok = $true
}
catch
{
    $Summary.error = $_.Exception.Message
    $Failures.Add($_.Exception.Message) | Out-Null
}
finally
{
    Write-GateOffConfig
}

$SummaryPath = Join-Path $Output "acceptance-summary.json"
$Summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $SummaryPath -Encoding utf8

Write-Host ""
Write-Host "P5 Agent E2E acceptance"
Write-Host "  summary : $SummaryPath"
Write-Host "  gateOff : $($Summary.stages.gateOff.errorCode)"
Write-Host "  full    : nodes=$($Summary.stages.fullSequence.nodeCount) edges=$($Summary.stages.fullSequence.edgeCount)"
Write-Host "  cross   : $($Summary.stages.crossProject.errorCode)"
Write-Host "  reload  : verified=$($Summary.stages.reload.verified) variables=$($Summary.stages.reload.variableCount)"
Write-Host "  result  : $($Summary.ok)"
if ($Summary.ok)
{
    exit 0
}
Write-Host "  error   : $($Summary.error)"
exit 1
