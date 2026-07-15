param(
    [string]$PythonExecutable = "",
    [switch]$IncludeDev,
    [switch]$Recreate,
    [switch]$UpgradePip
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

$ToolRoot = Get-UeakToolRoot
$VenvRoot = Join-Path $ToolRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$RuntimeRequirements = Join-Path $ToolRoot "requirements.lock"
$DevRequirements = Join-Path $ToolRoot "requirements-dev.lock"
$ValidationScript = Join-Path $PSScriptRoot "TestPythonEnvironment.py"

if ($Recreate -and (Test-Path -LiteralPath $VenvRoot))
{
    Remove-Item -LiteralPath $VenvRoot -Recurse -Force
}

if (!(Test-Path -LiteralPath $VenvPython -PathType Leaf))
{
    $BasePython = Resolve-UeakPythonExecutable -PythonExecutable $PythonExecutable
    Write-Host "Creating virtual environment..."
    Write-Host "  Base Python : $BasePython"
    Write-Host "  Environment : $VenvRoot"

    & $BasePython -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0)
    {
        throw "Failed to create virtual environment. Exit code: $LASTEXITCODE"
    }
}

if (!(Test-UeakPythonVersion -PythonExecutable $VenvPython))
{
    throw "The project virtual environment must use CPython 3.11 or 3.12: $VenvPython"
}

& $VenvPython -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0)
{
    throw "ensurepip failed with exit code $LASTEXITCODE"
}

if ($UpgradePip)
{
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0)
    {
        throw "pip upgrade failed with exit code $LASTEXITCODE"
    }
}

if (Test-UeakRequirementFileHasPackages -Path $RuntimeRequirements)
{
    & $VenvPython -m pip install --requirement $RuntimeRequirements
    if ($LASTEXITCODE -ne 0)
    {
        throw "Runtime dependency installation failed with exit code $LASTEXITCODE"
    }
}

if ($IncludeDev -and (Test-UeakRequirementFileHasPackages -Path $DevRequirements))
{
    & $VenvPython -m pip install --requirement $DevRequirements
    if ($LASTEXITCODE -ne 0)
    {
        throw "Development dependency installation failed with exit code $LASTEXITCODE"
    }
}

if (Test-Path -LiteralPath $ValidationScript -PathType Leaf)
{
    & $VenvPython $ValidationScript
    if ($LASTEXITCODE -ne 0)
    {
        throw "Python environment validation failed with exit code $LASTEXITCODE"
    }
}

Write-Host "PYTHON ENVIRONMENT READY"
Write-Host "Python : $VenvPython"
Write-Host "Version: $(& $VenvPython --version)"
