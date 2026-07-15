Set-StrictMode -Version Latest

$script:BctToolRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

function Get-BctToolRoot
{
    return $script:BctToolRoot
}

function Assert-BctPath
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

function Get-BctUniquePaths
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

function Test-BctEngineRoot
{
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return (Test-Path -LiteralPath (Join-Path $Path "Engine\Build\BatchFiles\Build.bat") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path "Engine\Binaries\Win64\UnrealEditor-Cmd.exe") -PathType Leaf)
}

function Get-BctEpicLauncherEngineRoots
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

function Get-BctRegistryEngineRoots
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

function Get-BctCommonEngineRoots
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

function Resolve-BctEngineRoot
{
    param(
        [string]$EngineRoot = "",
        [string]$EngineVersion = "5.6"
    )

    $ExplicitCandidates = @(
        $EngineRoot,
        $env:BCT_ENGINE_ROOT,
        $env:UE_ENGINE_ROOT
    )

    foreach ($Candidate in Get-BctUniquePaths -Paths $ExplicitCandidates)
    {
        if (Test-BctEngineRoot -Path $Candidate)
        {
            return $Candidate
        }

        throw "Configured Unreal Engine root is invalid: $Candidate"
    }

    $AutoCandidates = @()
    $AutoCandidates += @(Get-BctRegistryEngineRoots)
    $AutoCandidates += @(Get-BctEpicLauncherEngineRoots)
    $AutoCandidates += @(Get-BctCommonEngineRoots -EngineVersion $EngineVersion)

    foreach ($Candidate in Get-BctUniquePaths -Paths $AutoCandidates)
    {
        if (!(Test-BctEngineRoot -Path $Candidate))
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

    throw "Unreal Engine $EngineVersion was not found. Pass -EngineRoot or set BCT_ENGINE_ROOT."
}

function Get-BctMsvcToolchainFromRoot
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

function Get-BctVsWhereInstallations
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

    foreach ($VsWhere in Get-BctUniquePaths -Paths $VsWhereCandidates)
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

function Get-BctCommonMsvcRoots
{
    $Candidates = @()

    if ($env:VSINSTALLDIR)
    {
        $Candidates += Join-Path $env:VSINSTALLDIR "VC\Tools\MSVC"
    }

    foreach ($Installation in Get-BctVsWhereInstallations)
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

    return Get-BctUniquePaths -Paths $Candidates
}

function Resolve-BctMsvcToolchain
{
    param(
        [string]$MsvcToolsRoot = ""
    )

    $ConfiguredCandidates = @(
        $MsvcToolsRoot,
        $env:BCT_MSVC_TOOLS_ROOT
    )

    foreach ($Candidate in Get-BctUniquePaths -Paths $ConfiguredCandidates)
    {
        $Toolchain = Get-BctMsvcToolchainFromRoot -Path $Candidate
        if ($Toolchain)
        {
            return $Toolchain
        }

        throw "Configured MSVC tools path does not contain an x64 compiler: $Candidate"
    }

    $Toolchains = @()
    foreach ($Candidate in Get-BctCommonMsvcRoots)
    {
        $Toolchain = Get-BctMsvcToolchainFromRoot -Path $Candidate
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

    throw "An x64 MSVC toolchain was not found. Pass -MsvcToolsRoot or set BCT_MSVC_TOOLS_ROOT."
}

function Resolve-BctProjectPath
{
    param(
        [string]$ProjectPath = ""
    )

    $ConfiguredCandidates = @(
        $ProjectPath,
        $env:BCT_PROJECT_PATH
    )

    foreach ($Candidate in Get-BctUniquePaths -Paths $ConfiguredCandidates)
    {
        Assert-BctPath -Path $Candidate -Description "Unreal project file" -PathType File
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

    throw "Project path was not provided. Pass -ProjectPath or set BCT_PROJECT_PATH."
}

function Test-BctPythonVersion
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

function Resolve-BctPythonExecutable
{
    param(
        [string]$PythonExecutable = ""
    )

    $ConfiguredCandidates = @(
        $PythonExecutable,
        $env:BCT_PYTHON
    )

    foreach ($Candidate in Get-BctUniquePaths -Paths $ConfiguredCandidates)
    {
        if (Test-BctPythonVersion -PythonExecutable $Candidate)
        {
            return $Candidate
        }

        throw "Configured Python must be CPython 3.11 or 3.12: $Candidate"
    }

    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonCommand -and (Test-BctPythonVersion -PythonExecutable $PythonCommand.Source))
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
                    if (Test-BctPythonVersion -PythonExecutable $ResolvedPath.Trim())
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

    throw "CPython 3.11 or 3.12 was not found. Pass -PythonExecutable or set BCT_PYTHON."
}

function Test-BctRequirementFileHasPackages
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

function Ensure-BctJunction
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
    Assert-BctPath -Path $TargetPath -Description "Junction target" -PathType Directory

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

function Remove-BctJunction
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