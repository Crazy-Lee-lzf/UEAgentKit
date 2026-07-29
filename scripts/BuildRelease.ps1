param(
    [string]$EngineRoot = "",
    [string]$PythonExecutable = "",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$EngineRoot = Resolve-UeakEngineRoot -EngineRoot $EngineRoot
$PythonExecutable = Resolve-UeakPythonExecutable -PythonExecutable $PythonExecutable
$Version = (& $PythonExecutable -c "import tomllib; print(tomllib.load(open(r'$ToolRoot\pyproject.toml','rb'))['project']['version'])").Trim()
if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^\d+\.\d+\.\d+$')
{
    throw "Could not resolve a semantic project version."
}

$GitStatus = @(& git -C $ToolRoot status --porcelain)
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the Git worktree." }
if ($GitStatus.Count -ne 0)
{
    throw "Release builds require a clean Git worktree."
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory))
{
    $OutputDirectory = Join-Path $ToolRoot "Output\Release\$Version"
}
else
{
    $OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
}
$AllowedRoot = [IO.Path]::GetFullPath((Join-Path $ToolRoot "Output\Release"))
$AllowedPrefix = $AllowedRoot.TrimEnd([char]'\', [char]'/') + [IO.Path]::DirectorySeparatorChar
if (!$OutputDirectory.StartsWith($AllowedPrefix, [StringComparison]::OrdinalIgnoreCase))
{
    throw "Release output must stay below $AllowedRoot"
}
if (Test-Path -LiteralPath $OutputDirectory)
{
    Remove-Item -LiteralPath $OutputDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$RuffExecutable = Join-Path $ToolRoot ".venv\Scripts\ruff.exe"
if (Test-Path -LiteralPath $RuffExecutable -PathType Leaf)
{
    & $RuffExecutable check (Join-Path $ToolRoot "src") (Join-Path $ToolRoot "tests\python")
}
else
{
    & $PythonExecutable -m ruff check (Join-Path $ToolRoot "src") (Join-Path $ToolRoot "tests\python")
}
if ($LASTEXITCODE -ne 0) { throw "Ruff validation failed." }

& $PythonExecutable (Join-Path $ToolRoot "scripts\ValidateRelease.py") `
    --expected-version $Version `
    --require-release-docs `
    --skip-ruff
if ($LASTEXITCODE -ne 0) { throw "Portable release validation failed." }

$StagingRoot = Join-Path $OutputDirectory "Staging"
$PluginPackage = Join-Path $StagingRoot "UEAgentKit"
$BuildPlugin = Join-Path $ToolRoot "scripts\BuildPlugin.ps1"
& $BuildPlugin `
    -Method UAT `
    -EngineRoot $EngineRoot `
    -PackageDirectory $PluginPackage `
    -TargetPlatforms Win64
if ($LASTEXITCODE -ne 0) { throw "UE5.6 UAT plugin packaging failed." }

Copy-Item -LiteralPath (Join-Path $ToolRoot "LICENSE") -Destination (Join-Path $PluginPackage "LICENSE") -Force
Copy-Item -LiteralPath (Join-Path $ToolRoot "docs\RELEASE_$Version.md") -Destination (Join-Path $PluginPackage "RELEASE_NOTES.md") -Force
Copy-Item -LiteralPath (Join-Path $ToolRoot "docs\RELEASE_${Version}_EN.md") -Destination (Join-Path $PluginPackage "RELEASE_NOTES_EN.md") -Force

$PluginZip = Join-Path $OutputDirectory "UEAgentKit-$Version-UE5.6-Win64.zip"
Compress-Archive -LiteralPath $PluginPackage -DestinationPath $PluginZip -CompressionLevel Optimal

$PythonOutput = $OutputDirectory
& $PythonExecutable -m pip wheel $ToolRoot `
    --no-deps `
    --no-build-isolation `
    --wheel-dir $PythonOutput
if ($LASTEXITCODE -ne 0) { throw "Python wheel build failed." }
$Wheels = @(Get-ChildItem -LiteralPath $PythonOutput -Filter "ue_agent_kit-$Version-*.whl" -File)
if ($Wheels.Count -ne 1) { throw "Expected exactly one Python wheel; found $($Wheels.Count)." }
$Wheel = $Wheels[0]

$Artifacts = @($PluginZip, $Wheel.FullName)
$ChecksumLines = @()
$ArtifactRecords = @()
foreach ($Artifact in $Artifacts)
{
    $File = Get-Item -LiteralPath $Artifact
    $Hash = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $ChecksumLines += "$Hash  $($File.Name)"
    $ArtifactRecords += [ordered]@{
        fileName = $File.Name
        sha256 = $Hash
        size = [int64]$File.Length
    }
}
[IO.File]::WriteAllText(
    (Join-Path $OutputDirectory "SHA256SUMS.txt"),
    (($ChecksumLines -join "`r`n") + "`r`n"),
    [Text.UTF8Encoding]::new($false))

$Commit = (& git -C $ToolRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not resolve release commit." }
$Manifest = [ordered]@{
    schemaVersion = "1.0"
    product = "UE Agent Kit"
    version = $Version
    unrealEngine = "5.6"
    targetPlatform = "Win64"
    gitCommit = $Commit
    createdUtc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    gates = @(
        "release-validation",
        "ruff",
        "python-tests",
        "schema-validation",
        "uat-plugin-package",
        "python-wheel"
    )
    artifacts = $ArtifactRecords
}
[IO.File]::WriteAllText(
    (Join-Path $OutputDirectory "release-manifest.json"),
    (($Manifest | ConvertTo-Json -Depth 10) + "`r`n"),
    [Text.UTF8Encoding]::new($false))
Remove-Item -LiteralPath $StagingRoot -Recurse -Force

Write-Host "RELEASE BUILD SUCCEEDED"
Write-Host "Version : $Version"
Write-Host "Output  : $OutputDirectory"
Get-Content -LiteralPath (Join-Path $OutputDirectory "SHA256SUMS.txt")
