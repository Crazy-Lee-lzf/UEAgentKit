Set-StrictMode -Version Latest

$script:UeakToolRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

function Get-UeakToolRoot
{
    return $script:UeakToolRoot
}

function Assert-UeakPath
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Description,

        [ValidateSet("Any", "File", "Directory")]
        [string]$PathType = "Any"
    )

    $Exists = switch ($PathType)
    {
        "File" { Test-Path -LiteralPath $Path -PathType Leaf }
        "Directory" { Test-Path -LiteralPath $Path -PathType Container }
        default { Test-Path -LiteralPath $Path }
    }

    if (!$Exists)
    {
        throw "$Description not found: $Path"
    }
}

function Get-UeakUniquePaths
{
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [string[]]$Paths
    )

    $Seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($Path in $Paths)
    {
        if ([string]::IsNullOrWhiteSpace($Path))
        {
            continue
        }

        try
        {
            $FullPath = [System.IO.Path]::GetFullPath($Path.Trim().Trim('"'))
        }
        catch
        {
            continue
        }

        if ($Seen.Add($FullPath))
        {
            $FullPath
        }
    }
}

function Test-UeakEngineRoot
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return (Test-Path -LiteralPath (Join-Path $Path "Engine\Build\BatchFiles\Build.bat") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path "Engine\Binaries\Win64\UnrealEditor-Cmd.exe") -PathType Leaf)
}

function Get-UeakEpicLauncherEngineRoots
{
    $CommonApplicationData = $env:ProgramData
    if ([string]::IsNullOrWhiteSpace($CommonApplicationData))
    {
        try
        {
            $CommonApplicationData = [Environment]::GetFolderPath([Environment+SpecialFolder]::CommonApplicationData)
        }
        catch
        {
            $CommonApplicationData = ""
        }
    }
    if ([string]::IsNullOrWhiteSpace($CommonApplicationData) -and $env:SystemDrive)
    {
        $CommonApplicationData = Join-Path $env:SystemDrive "ProgramData"
    }
    if ([string]::IsNullOrWhiteSpace($CommonApplicationData) -and (Test-Path -LiteralPath "C:\ProgramData" -PathType Container))
    {
        $CommonApplicationData = "C:\ProgramData"
    }
    if ([string]::IsNullOrWhiteSpace($CommonApplicationData))
    {
        return
    }

    $LauncherFile = Join-Path $CommonApplicationData "Epic\UnrealEngineLauncher\LauncherInstalled.dat"
    if (!(Test-Path -LiteralPath $LauncherFile -PathType Leaf))
    {
        return
    }

    try
    {
        $LauncherData = Get-Content -LiteralPath $LauncherFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($Installation in @($LauncherData.InstallationList))
        {
            if ($Installation.InstallLocation)
            {
                [string]$Installation.InstallLocation
            }
        }
    }
    catch
    {
        Write-Verbose "Failed to parse Epic Launcher installation data: $($_.Exception.Message)"
    }
}

function Get-UeakRegistryEngineRoots
{
    $Keys = @(
        "HKLM:\SOFTWARE\EpicGames\Unreal Engine\5.6",
        "HKLM:\SOFTWARE\WOW6432Node\EpicGames\Unreal Engine\5.6"
    )

    foreach ($Key in $Keys)
    {
        try
        {
            $Properties = Get-ItemProperty -LiteralPath $Key -ErrorAction Stop
            if ($Properties.InstalledDirectory)
            {
                [string]$Properties.InstalledDirectory
            }
        }
        catch
        {
        }
    }

    try
    {
        $BuildsKey = "HKCU:\SOFTWARE\Epic Games\Unreal Engine\Builds"
        if (Test-Path -LiteralPath $BuildsKey)
        {
            $Properties = Get-ItemProperty -LiteralPath $BuildsKey
            foreach ($Property in $Properties.PSObject.Properties)
            {
                if ($Property.Name -notmatch '^PS' -and $Property.Value -is [string])
                {
                    [string]$Property.Value
                }
            }
        }
    }
    catch
    {
    }
}

function Get-UeakCommonEngineRoots
{
    param(
        [string]$EngineVersion = "5.6"
    )

    $RelativeCandidates = @(
        "Epic Games\UE_$EngineVersion",
        "EPICGAME\UE_$EngineVersion",
        "UnrealEngine\UE_$EngineVersion",
        "UE_$EngineVersion"
    )

    foreach ($Drive in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)
    {
        foreach ($RelativePath in $RelativeCandidates)
        {
            Join-Path $Drive.Root $RelativePath
        }
    }
}

function Resolve-UeakEngineRoot
{
    param(
        [string]$EngineRoot = "",
        [string]$EngineVersion = "5.6"
    )

    $ExplicitCandidates = @(
        $EngineRoot,
        $env:UEAK_ENGINE_ROOT,
        $env:UE_ENGINE_ROOT
    )

    foreach ($Candidate in Get-UeakUniquePaths -Paths $ExplicitCandidates)
    {
        if (Test-UeakEngineRoot -Path $Candidate)
        {
            return $Candidate
        }

        throw "Configured Unreal Engine root is invalid: $Candidate"
    }

    $AutoCandidates = @()
    $AutoCandidates += @(Get-UeakRegistryEngineRoots)
    $AutoCandidates += @(Get-UeakEpicLauncherEngineRoots)
    $AutoCandidates += @(Get-UeakCommonEngineRoots -EngineVersion $EngineVersion)

    foreach ($Candidate in Get-UeakUniquePaths -Paths $AutoCandidates)
    {
        if (!(Test-UeakEngineRoot -Path $Candidate))
        {
            continue
        }

        $BuildVersionFile = Join-Path $Candidate "Engine\Build\Build.version"
        if (Test-Path -LiteralPath $BuildVersionFile -PathType Leaf)
        {
            try
            {
                $BuildVersion = Get-Content -LiteralPath $BuildVersionFile -Raw -Encoding UTF8 | ConvertFrom-Json
                $DetectedVersion = "$($BuildVersion.MajorVersion).$($BuildVersion.MinorVersion)"
                if ($DetectedVersion -ne $EngineVersion)
                {
                    continue
                }
            }
            catch
            {
                Write-Verbose "Could not parse Build.version under $Candidate"
            }
        }

        return $Candidate
    }

    throw "Unreal Engine $EngineVersion was not found. Pass -EngineRoot or set UEAK_ENGINE_ROOT."
}

function Get-UeakMsvcToolchainFromRoot
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (!(Test-Path -LiteralPath $Path -PathType Container))
    {
        return $null
    }

    $DirectCompiler = Join-Path $Path "bin\Hostx64\x64\cl.exe"
    if (Test-Path -LiteralPath $DirectCompiler -PathType Leaf)
    {
        return Get-Item -LiteralPath $Path
    }

    $Candidates = @()
    foreach ($Directory in Get-ChildItem -LiteralPath $Path -Directory -ErrorAction SilentlyContinue)
    {
        $Compiler = Join-Path $Directory.FullName "bin\Hostx64\x64\cl.exe"
        if (Test-Path -LiteralPath $Compiler -PathType Leaf)
        {
            $Candidates += $Directory
        }
    }

    return $Candidates |
        Sort-Object { try { [version]$_.Name } catch { [version]"0.0" } } -Descending |
        Select-Object -First 1
}

function Get-UeakVsWhereInstallations
{
    $VsWhereCandidates = @()
    $VsWhereCommand = Get-Command vswhere.exe -ErrorAction SilentlyContinue
    if ($VsWhereCommand)
    {
        $VsWhereCandidates += $VsWhereCommand.Source
    }

    if (${env:ProgramFiles(x86)})
    {
        $VsWhereCandidates += Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    }

    foreach ($VsWhere in Get-UeakUniquePaths -Paths $VsWhereCandidates)
    {
        if (!(Test-Path -LiteralPath $VsWhere -PathType Leaf))
        {
            continue
        }

        try
        {
            $Output = & $VsWhere -all -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
            foreach ($Line in @($Output))
            {
                if (![string]::IsNullOrWhiteSpace($Line))
                {
                    $Line.Trim()
                }
            }
        }
        catch
        {
            Write-Verbose "vswhere failed: $($_.Exception.Message)"
        }
    }
}

function Get-UeakCommonMsvcRoots
{
    $Candidates = @()

    if ($env:VSINSTALLDIR)
    {
        $Candidates += Join-Path $env:VSINSTALLDIR "VC\Tools\MSVC"
    }

    foreach ($Installation in Get-UeakVsWhereInstallations)
    {
        $Candidates += Join-Path $Installation "VC\Tools\MSVC"
    }

    foreach ($Drive in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)
    {
        foreach ($ProgramDirectory in @("Program Files (x86)", "Program Files"))
        {
            $VsRoot = Join-Path $Drive.Root "$ProgramDirectory\Microsoft Visual Studio"
            if (!(Test-Path -LiteralPath $VsRoot -PathType Container))
            {
                continue
            }

            foreach ($YearDirectory in Get-ChildItem -LiteralPath $VsRoot -Directory -ErrorAction SilentlyContinue)
            {
                foreach ($EditionDirectory in Get-ChildItem -LiteralPath $YearDirectory.FullName -Directory -ErrorAction SilentlyContinue)
                {
                    $Candidates += Join-Path $EditionDirectory.FullName "VC\Tools\MSVC"
                }
            }
        }
    }

    return Get-UeakUniquePaths -Paths $Candidates
}

function Resolve-UeakMsvcToolchain
{
    param(
        [string]$MsvcToolsRoot = ""
    )

    $ConfiguredCandidates = @(
        $MsvcToolsRoot,
        $env:UEAK_MSVC_TOOLS_ROOT
    )

    foreach ($Candidate in Get-UeakUniquePaths -Paths $ConfiguredCandidates)
    {
        $Toolchain = Get-UeakMsvcToolchainFromRoot -Path $Candidate
        if ($Toolchain)
        {
            return $Toolchain
        }

        throw "Configured MSVC tools path does not contain an x64 compiler: $Candidate"
    }

    $Toolchains = @()
    foreach ($Candidate in Get-UeakCommonMsvcRoots)
    {
        $Toolchain = Get-UeakMsvcToolchainFromRoot -Path $Candidate
        if ($Toolchain)
        {
            $Toolchains += $Toolchain
        }
    }

    $Selected = $Toolchains |
        Sort-Object { try { [version]$_.Name } catch { [version]"0.0" } } -Descending |
        Select-Object -First 1

    if ($Selected)
    {
        return $Selected
    }

    throw "An x64 MSVC toolchain was not found. Pass -MsvcToolsRoot or set UEAK_MSVC_TOOLS_ROOT."
}

function Resolve-UeakProjectPath
{
    param(
        [string]$ProjectPath = ""
    )

    $ConfiguredCandidates = @(
        $ProjectPath,
        $env:UEAK_PROJECT_PATH
    )

    foreach ($Candidate in Get-UeakUniquePaths -Paths $ConfiguredCandidates)
    {
        Assert-UeakPath -Path $Candidate -Description "Unreal project file" -PathType File
        if ([System.IO.Path]::GetExtension($Candidate) -ne ".uproject")
        {
            throw "Project path must point to a .uproject file: $Candidate"
        }

        return $Candidate
    }

    $CurrentProjects = @(Get-ChildItem -LiteralPath (Get-Location).Path -Filter *.uproject -File -ErrorAction SilentlyContinue)
    if ($CurrentProjects.Count -eq 1)
    {
        return $CurrentProjects[0].FullName
    }

    throw "Project path was not provided. Pass -ProjectPath or set UEAK_PROJECT_PATH."
}

function Test-UeakPythonVersion
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExecutable
    )

    if (!(Test-Path -LiteralPath $PythonExecutable -PathType Leaf))
    {
        return $false
    }

    try
    {
        & $PythonExecutable -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)"
        return $LASTEXITCODE -eq 0
    }
    catch
    {
        return $false
    }
}

function Resolve-UeakPythonExecutable
{
    param(
        [string]$PythonExecutable = ""
    )

    $ConfiguredCandidates = @(
        $PythonExecutable,
        $env:UEAK_PYTHON
    )

    foreach ($Candidate in Get-UeakUniquePaths -Paths $ConfiguredCandidates)
    {
        if (Test-UeakPythonVersion -PythonExecutable $Candidate)
        {
            return $Candidate
        }

        throw "Configured Python must be CPython 3.11 or 3.12: $Candidate"
    }

    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonCommand -and (Test-UeakPythonVersion -PythonExecutable $PythonCommand.Source))
    {
        return $PythonCommand.Source
    }

    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($PyLauncher)
    {
        foreach ($Version in @("3.12", "3.11"))
        {
            try
            {
                $Resolved = & $PyLauncher.Source "-$Version" -c "import sys; print(sys.executable)"
                if ($LASTEXITCODE -eq 0 -and $Resolved)
                {
                    $ResolvedPath = [string]$Resolved | Select-Object -First 1
                    if (Test-UeakPythonVersion -PythonExecutable $ResolvedPath.Trim())
                    {
                        return $ResolvedPath.Trim()
                    }
                }
            }
            catch
            {
            }
        }
    }

    throw "CPython 3.11 or 3.12 was not found. Pass -PythonExecutable or set UEAK_PYTHON."
}

function Test-UeakRequirementFileHasPackages
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (!(Test-Path -LiteralPath $Path -PathType Leaf))
    {
        return $false
    }

    foreach ($Line in Get-Content -LiteralPath $Path -Encoding UTF8)
    {
        $Trimmed = $Line.Trim()
        if ($Trimmed -and !$Trimmed.StartsWith("#"))
        {
            return $true
        }
    }

    return $false
}

function Ensure-UeakJunction
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$LinkPath,

        [Parameter(Mandatory = $true)]
        [string]$TargetPath,

        [switch]$ReplaceDifferentJunction
    )

    $LinkPath = [System.IO.Path]::GetFullPath($LinkPath)
    $TargetPath = [System.IO.Path]::GetFullPath($TargetPath)
    Assert-UeakPath -Path $TargetPath -Description "Junction target" -PathType Directory

    if (Test-Path -LiteralPath $LinkPath)
    {
        $Item = Get-Item -LiteralPath $LinkPath -Force
        $ExistingTargets = @($Item.Target | ForEach-Object { [System.IO.Path]::GetFullPath($_) })
        if ($Item.LinkType -eq "Junction" -and $ExistingTargets -contains $TargetPath)
        {
            return $Item
        }

        if ($Item.LinkType -ne "Junction")
        {
            throw "Refusing to replace a non-junction path: $LinkPath"
        }

        if (!$ReplaceDifferentJunction)
        {
            throw "Junction points to a different target: $LinkPath"
        }

        Remove-Item -LiteralPath $LinkPath -Force
    }

    $Parent = Split-Path -Parent $LinkPath
    New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    return New-Item -ItemType Junction -Path $LinkPath -Target $TargetPath
}

function Remove-UeakJunction
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$LinkPath
    )

    if (!(Test-Path -LiteralPath $LinkPath))
    {
        return $false
    }

    $Item = Get-Item -LiteralPath $LinkPath -Force
    if ($Item.LinkType -ne "Junction")
    {
        throw "Refusing to remove a non-junction path: $LinkPath"
    }

    Remove-Item -LiteralPath $LinkPath -Force
    return $true
}