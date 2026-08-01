param(
    [string]$EngineRoot = "",
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,
    [ValidateRange(30, 300)]
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "TestMcpLiveWriteRegression.ps1") `
    -EngineRoot $EngineRoot `
    -ProjectPath $ProjectPath `
    -StartupTimeoutSeconds $StartupTimeoutSeconds `
    -Suite Fast
