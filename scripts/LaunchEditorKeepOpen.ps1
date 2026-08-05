param(
    [string]$EngineRoot = "E:\EPICGAME\UE_5.6",
    [string]$ProjectPath = "E:\WorkSpace\我的项目\我的项目.uproject",
    [ValidateRange(30, 600)]
    [int]$StartupTimeoutSeconds = 300
)

# Launches the Unreal Editor with the UE Agent Kit plugin and leaves it running
# so the Live Editor Bridge stays available for the retarget workflow.

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$ProjectDirectory = Split-Path -Parent $ProjectPath
$UnrealEditor = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
Assert-UeakPath -Path $UnrealEditor -Description "UnrealEditor.exe" -PathType File

$DescriptorPath = Join-Path $ProjectDirectory "Saved\UEAgentKit\EditorBridge.json"

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

$ExistingDescriptor = Read-BridgeDescriptor
if ($null -ne $ExistingDescriptor)
{
    $ExistingProcess = Get-Process -Id ([int]$ExistingDescriptor.processId) -ErrorAction SilentlyContinue
    if ($null -ne $ExistingProcess)
    {
        Write-Host "A Live Editor Bridge is already active (PID $($ExistingProcess.Id))."
        exit 0
    }
    Remove-Item -LiteralPath $DescriptorPath -Force -ErrorAction SilentlyContinue
}

$Output = Join-Path (Get-UeakToolRoot) "Output\EditorLaunch"
New-Item -ItemType Directory -Path $Output -Force | Out-Null
$EditorStdout = Join-Path $Output "Editor-stdout.log"
$EditorStderr = Join-Path $Output "Editor-stderr.log"

$EditorProcess = Start-Process `
    -FilePath $UnrealEditor `
    -ArgumentList @(
        $ProjectPath,
        "-unattended",
        "-nosplash",
        "-NoSound",
        "-NoP4",
        "-stdout",
        "-FullStdOutLogOutput") `
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
    if ($null -ne $Descriptor -and [int]$Descriptor.processId -eq $EditorProcess.Id -and [int]$Descriptor.port -gt 0)
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

Write-Host "Live Editor Bridge ready (PID $($EditorProcess.Id), port $($Descriptor.port)). Editor left running."
