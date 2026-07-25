param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [switch]$UseExistingEditor,
    [switch]$UseRHI,
    [string]$StartupMap = "",
    [string]$ActorGuid = "",
    [ValidateRange(30, 300)]
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$VenvPython = Join-Path $ToolRoot ".venv\Scripts\python.exe"
$TestScript = Join-Path $ToolRoot "tests\integration\mcp_live_editor_smoke.py"
$UnrealEditor = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
$ProjectDirectory = Split-Path -Parent $ProjectPath
$DescriptorPath = Join-Path $ProjectDirectory "Saved\UEAgentKit\EditorBridge.json"
$OutputRoot = Join-Path $ToolRoot "Output\McpLiveEditorSmoke"
$EditorStdout = Join-Path $OutputRoot "Editor-stdout.log"
$EditorStderr = Join-Path $OutputRoot "Editor-stderr.log"

Assert-UeakPath -Path $VenvPython -Description "project Python environment" -PathType File
Assert-UeakPath -Path $TestScript -Description "MCP Live Editor smoke test" -PathType File
Assert-UeakPath -Path $UnrealEditor -Description "UnrealEditor.exe" -PathType File
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

function Read-BridgeDescriptor
{
    if (!(Test-Path -LiteralPath $DescriptorPath))
    {
        return $null
    }
    try
    {
        $Text = [System.IO.File]::ReadAllText(
            $DescriptorPath,
            [System.Text.UTF8Encoding]::new($false))
        return $Text | ConvertFrom-Json
    }
    catch
    {
        return $null
    }
}

$EditorProcess = $null
try
{
    if (!$UseExistingEditor)
    {
        $ExistingDescriptor = Read-BridgeDescriptor
        if ($null -ne $ExistingDescriptor)
        {
            $ExistingProcess = Get-Process -Id ([int]$ExistingDescriptor.processId) -ErrorAction SilentlyContinue
            if ($null -ne $ExistingProcess)
            {
                throw "A Live Editor Bridge is already active for this project. Use -UseExistingEditor or close that Editor first."
            }
            Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $EditorStdout, $EditorStderr -Force -ErrorAction SilentlyContinue
        $EditorArguments = @(
            $ProjectPath
        )
        if (![string]::IsNullOrWhiteSpace($StartupMap))
        {
            $EditorArguments += $StartupMap
        }
        $EditorArguments += @(
            "-unattended",
            "-nosplash",
            "-NoSound",
            "-NoP4",
            "-stdout",
            "-FullStdOutLogOutput"
        )
        if (!$UseRHI)
        {
            $EditorArguments += "-NullRHI"
        }
        $EditorProcess = Start-Process `
            -FilePath $UnrealEditor `
            -ArgumentList $EditorArguments `
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
    }
    else
    {
        $Descriptor = Read-BridgeDescriptor
        if ($null -eq $Descriptor)
        {
            throw "UseExistingEditor requires an active Live Editor Bridge descriptor."
        }
        $ExistingProcess = Get-Process -Id ([int]$Descriptor.processId) -ErrorAction SilentlyContinue
        if ($null -eq $ExistingProcess)
        {
            throw "The existing Live Editor Bridge descriptor is stale."
        }
    }

    $TestArguments = @($TestScript, "--project", $ProjectPath)
    if (![string]::IsNullOrWhiteSpace($StartupMap))
    {
        $TestArguments += @("--startup-map", $StartupMap)
    }
    if (![string]::IsNullOrWhiteSpace($ActorGuid))
    {
        $TestArguments += @("--actor-guid", $ActorGuid)
    }
    & $VenvPython @TestArguments
    if ($LASTEXITCODE -ne 0)
    {
        throw "MCP Live Editor smoke test failed with exit code $LASTEXITCODE"
    }
}
finally
{
    if ($null -ne $EditorProcess)
    {
        if (!$EditorProcess.HasExited)
        {
            Stop-Process -Id $EditorProcess.Id -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $EditorProcess.Id -Timeout 15 -ErrorAction SilentlyContinue
        }
        $Descriptor = Read-BridgeDescriptor
        if ($null -ne $Descriptor -and [int]$Descriptor.processId -eq $EditorProcess.Id)
        {
            Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue
        }
    }
}
