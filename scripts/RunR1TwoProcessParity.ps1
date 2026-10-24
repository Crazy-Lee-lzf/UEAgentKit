param(
    [string]$EngineRoot = "E:\EPICGAME\UE_5.6",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "RunR1NativeTests.ps1"

# Serial by design: only one heavy UE editor/process at a time.
& $Runner -EngineRoot $EngineRoot -SkipBuild:$SkipBuild -Filter "UEAgentKit.Native.R1" -Label "run-a"
& $Runner -EngineRoot $EngineRoot -SkipBuild -Filter "UEAgentKit.Native.R1" -Label "run-b"

Push-Location $RepoRoot
try {
    & py -3.12 -m unittest tests.native_tools.test_r1_cross_process -v
    if ($LASTEXITCODE -ne 0) {
        throw "two-process parity tests failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
