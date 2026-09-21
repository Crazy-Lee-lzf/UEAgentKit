param(
    [string]$EngineRoot = "",
    [string]$ProjectPath = "",
    [string]$Database = "",
    [string]$Output = "",
    [string]$ProjectKey = "",
    [string[]]$SourceRoot = @("Source"),
    [string]$PythonExecutable = "",
    [switch]$ForceCodeIndex,
    [switch]$NoPrune,
    [switch]$CodeOnly,
    [switch]$CompactJson
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$ProjectPath = Resolve-UeakProjectPath -ProjectPath $ProjectPath
$ProjectRoot = Split-Path -Parent $ProjectPath
$PythonExecutable = Resolve-UeakPythonExecutable -PythonExecutable $PythonExecutable
$CliPath = Join-Path $PSScriptRoot "ue-agent.py"
Assert-UeakPath -Path $CliPath -Description "ue-agent.py" -PathType File

if ([string]::IsNullOrWhiteSpace($ProjectKey))
{
    $ProjectKey = [System.IO.Path]::GetFileNameWithoutExtension($ProjectPath)
}
if ([string]::IsNullOrWhiteSpace($ProjectKey))
{
    throw "ProjectKey could not be derived from ProjectPath."
}

if ([string]::IsNullOrWhiteSpace($Database))
{
    $Database = Join-Path $ToolRoot ".data\ue_agent_kit.sqlite3"
}
elseif (![System.IO.Path]::IsPathRooted($Database))
{
    $Database = Join-Path $ToolRoot $Database
}
$Database = [System.IO.Path]::GetFullPath($Database)
New-Item -ItemType Directory -Path (Split-Path -Parent $Database) -Force | Out-Null

if ([string]::IsNullOrWhiteSpace($Output))
{
    $Output = Join-Path $ToolRoot ("Output\CodeKnowledge\" + $ProjectKey)
}
elseif (![System.IO.Path]::IsPathRooted($Output))
{
    $Output = Join-Path $ToolRoot $Output
}
$Output = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path $Output -Force | Out-Null

function Invoke-UeakCliJson
{
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $Raw = & $PythonExecutable $CliPath @Arguments | Out-String
    if ($LASTEXITCODE -ne 0)
    {
        throw "ue-agent.py failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
    try
    {
        return $Raw | ConvertFrom-Json
    }
    catch
    {
        throw "ue-agent.py returned invalid JSON for: $($Arguments -join ' ')"
    }
}

$CodeArguments = @(
    "index",
    "code",
    $ProjectRoot,
    "--database",
    $Database,
    "--project-key",
    $ProjectKey
)
foreach ($Root in $SourceRoot)
{
    if (![string]::IsNullOrWhiteSpace($Root))
    {
        $CodeArguments += @("--source-root", $Root)
    }
}
if ($ForceCodeIndex)
{
    $CodeArguments += "--force"
}
if ($NoPrune)
{
    $CodeArguments += "--no-prune"
}

Write-Host "Building C++ Code Index..."
Write-Host "Project : $ProjectPath"
Write-Host "Database: $Database"
$CodeResult = Invoke-UeakCliJson -Arguments $CodeArguments

$ReflectionResult = $null
$ReflectionFile = ""
if (!$CodeOnly)
{
    $ReflectionDirectory = Join-Path $Output "Reflection"
    $ReflectionArguments = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        (Join-Path $PSScriptRoot "RunReflectionExport.ps1"),
        "-EngineRoot",
        $EngineRoot,
        "-ProjectPath",
        $ProjectPath,
        "-Output",
        $ReflectionDirectory
    )
    if ($CompactJson)
    {
        $ReflectionArguments += "-CompactJson"
    }

    Write-Host "Exporting UE Reflection..."
    & powershell.exe @ReflectionArguments
    if ($LASTEXITCODE -ne 0)
    {
        throw "RunReflectionExport.ps1 failed with exit code $LASTEXITCODE"
    }

    $ReflectionFile = Join-Path $ReflectionDirectory "reflection.json"
    Assert-UeakPath -Path $ReflectionFile -Description "reflection.json" -PathType File

    Write-Host "Merging UE Reflection into Code Index..."
    $ReflectionResult = Invoke-UeakCliJson -Arguments @(
        "index",
        "reflection",
        $ReflectionFile,
        "--database",
        $Database,
        "--project-key",
        $ProjectKey
    )
}

$Summary = [ordered]@{
    schemaVersion = "code-knowledge-workflow-1.0"
    projectKey = $ProjectKey
    projectPath = $ProjectPath
    projectRoot = $ProjectRoot
    database = $Database
    sourceRoots = @($SourceRoot)
    codeIndex = $CodeResult
    reflectionFile = $ReflectionFile
    reflection = $ReflectionResult
}

$SummaryPath = Join-Path $Output "code_knowledge_summary.json"
$Json = if ($CompactJson)
{
    $Summary | ConvertTo-Json -Depth 32 -Compress
}
else
{
    $Summary | ConvertTo-Json -Depth 32
}
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($SummaryPath, $Json + [Environment]::NewLine, $Utf8NoBom)

Write-Host "Code Knowledge workflow completed."
Write-Host "Summary : $SummaryPath"
Write-Output $Json
