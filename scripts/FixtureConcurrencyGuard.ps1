# DEF-07 fixture concurrency guard.
#
# Read-only by construction: this module enumerates Win32_Process and inspects
# command lines.  It never terminates, signals or reconfigures another process,
# and it never touches another tool's descriptor.
#
# The guard answers one question before a fixture commandlet may write:
# "is any Unreal Editor process holding this exact .uproject right now?"
# Unknown state always blocks.  Different projects are allowed.

$script:UeakFixtureGuardSchemaVersion = "ueak.fixture-concurrency-guard/1.0"
$script:UeakEditorProcessPattern = '^UnrealEditor[^\\/]*\.exe$'
$script:UeakGuardCodeClear = "fixture-project-lock-clear"
$script:UeakGuardCodeInUse = "fixture-project-in-use"
$script:UeakGuardCodeUnverifiable = "fixture-project-lock-unverifiable"

function Split-UeakCommandLineArguments
{
    param([AllowNull()][string]$CommandLine)

    $Result = New-Object System.Collections.Generic.List[string]
    if ($null -eq $CommandLine)
    {
        return @()
    }

    $Builder = New-Object System.Text.StringBuilder
    $InQuotes = $false
    $HasToken = $false
    $Index = 0
    while ($Index -lt $CommandLine.Length)
    {
        $Character = $CommandLine[$Index]
        if ($Character -eq '\' -and ($Index + 1) -lt $CommandLine.Length -and $CommandLine[$Index + 1] -eq '"')
        {
            [void]$Builder.Append('"')
            $HasToken = $true
            $Index += 2
            continue
        }
        if ($Character -eq '"')
        {
            $InQuotes = !$InQuotes
            $HasToken = $true
            $Index++
            continue
        }
        if (!$InQuotes -and [char]::IsWhiteSpace($Character))
        {
            if ($HasToken -or $Builder.Length -gt 0)
            {
                $Result.Add($Builder.ToString())
                [void]$Builder.Clear()
                $HasToken = $false
            }
            $Index++
            continue
        }
        [void]$Builder.Append($Character)
        $HasToken = $true
        $Index++
    }
    if ($HasToken -or $Builder.Length -gt 0)
    {
        $Result.Add($Builder.ToString())
    }

    return $Result.ToArray()
}

function Get-UeakProjectArgumentsFromCommandLine
{
    param([AllowNull()][string]$CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine))
    {
        return @()
    }

    $Paths = New-Object System.Collections.Generic.List[string]
    $Tokens = @(Split-UeakCommandLineArguments -CommandLine $CommandLine)
    for ($Index = 0; $Index -lt $Tokens.Count; $Index++)
    {
        $Token = [string]$Tokens[$Index]
        if ([string]::IsNullOrWhiteSpace($Token))
        {
            continue
        }
        if ($Token -match '^-project=(?<path>.+)$')
        {
            $Paths.Add([string]$Matches['path'])
            continue
        }
        if ($Token -eq '-project')
        {
            if (($Index + 1) -lt $Tokens.Count)
            {
                $Paths.Add([string]$Tokens[$Index + 1])
            }
            continue
        }
        if ($Token -match '\.uproject$')
        {
            $Paths.Add($Token)
        }
    }

    return $Paths.ToArray()
}

function Resolve-UeakAbsolutePath
{
    param([AllowNull()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path))
    {
        return $null
    }

    # A command line may quote a path; strip one layer of quotes and padding.
    $Trimmed = ([string]$Path).Trim()
    if ($Trimmed.Length -ge 2 -and $Trimmed.StartsWith('"') -and $Trimmed.EndsWith('"'))
    {
        $Trimmed = $Trimmed.Substring(1, $Trimmed.Length - 2).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($Trimmed))
    {
        return $null
    }

    # A relative path cannot be resolved reliably without the process working
    # directory, which Win32_Process does not expose.  Callers treat null as
    # "ambiguous" and block.
    if (![System.IO.Path]::IsPathRooted($Trimmed))
    {
        return $null
    }

    try
    {
        $Full = [System.IO.Path]::GetFullPath($Trimmed)
    }
    catch
    {
        return $null
    }
    if ([string]::IsNullOrWhiteSpace($Full))
    {
        return $null
    }

    $Full = $Full -replace '/', '\'
    # Trim trailing separators but stay rooted: keep "C:\" and let the UNC
    # share root settle at "\\server\share".
    while ($Full.Length -gt 3 -and $Full.EndsWith('\'))
    {
        $Shorter = $Full.Substring(0, $Full.Length - 1)
        if ($Shorter -match '^[A-Za-z]:$')
        {
            break
        }
        $Full = $Shorter
    }

    return $Full
}

function Get-UeakItemLinkTargets
{
    param([Parameter(Mandatory = $true)]$Item)

    $Property = $Item.PSObject.Properties['Target']
    if ($null -eq $Property)
    {
        $Property = $Item.PSObject.Properties['LinkTarget']
    }
    if ($null -eq $Property)
    {
        return @()
    }

    $Targets = @($Property.Value) | Where-Object { !$_.Equals($null) -and ![string]::IsNullOrWhiteSpace([string]$_) }
    return @($Targets)
}

function Resolve-UeakPathState
{
    param([AllowNull()][string]$Path)

    $Invalid = @{ Normalized = $null; Resolved = $null; State = "invalid" }
    $Normalized = Resolve-UeakAbsolutePath -Path $Path
    if ($null -eq $Normalized)
    {
        return $Invalid
    }

    $Root = [System.IO.Path]::GetPathRoot($Normalized)
    if ([string]::IsNullOrEmpty($Root))
    {
        return $Invalid
    }

    $Current = $Root
    $Linked = $false
    $Remainder = $Normalized.Substring($Root.Length)
    $Segments = @($Remainder -split '[\\/]' | Where-Object { $_.Length -gt 0 })
    foreach ($Segment in $Segments)
    {
        if ($Current.EndsWith('\'))
        {
            $Next = $Current + $Segment
        }
        else
        {
            $Next = $Current + '\' + $Segment
        }

        $Item = $null
        try
        {
            $Item = Get-Item -LiteralPath $Next -Force -ErrorAction Stop
        }
        catch
        {
            # A component does not exist.  Without a link there is nothing to
            # resolve, so the literal path is authoritative.
            return @{ Normalized = $Normalized; Resolved = $null; State = "missing" }
        }

        if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
        {
            $Targets = @(Get-UeakItemLinkTargets -Item $Item)
            if ($Targets.Count -ne 1)
            {
                return @{ Normalized = $Normalized; Resolved = $null; State = "ambiguous" }
            }
            $TargetPath = [string]$Targets[0]
            if (![System.IO.Path]::IsPathRooted($TargetPath))
            {
                $Parent = [System.IO.Path]::GetDirectoryName($Next)
                if ([string]::IsNullOrEmpty($Parent))
                {
                    return @{ Normalized = $Normalized; Resolved = $null; State = "ambiguous" }
                }
                $TargetPath = $Parent + '\' + $TargetPath
            }
            $ResolvedTarget = Resolve-UeakAbsolutePath -Path $TargetPath
            if ($null -eq $ResolvedTarget)
            {
                return @{ Normalized = $Normalized; Resolved = $null; State = "ambiguous" }
            }
            $Current = $ResolvedTarget
            $Linked = $true
        }
        else
        {
            $Current = $Next
        }
    }

    $State = "exact"
    if ($Linked)
    {
        $State = "linked"
    }
    return @{ Normalized = $Normalized; Resolved = $Current; State = $State }
}

function Get-UeakProjectPathStates
{
    param([AllowNull()][string]$Path)

    $State = Resolve-UeakPathState -Path $Path
    if ($State.State -eq "missing")
    {
        # The project file itself must exist for a fixture run; a missing
        # target is a caller error, not a concurrency answer.
        return @()
    }
    if ($State.State -eq "invalid" -or $State.State -eq "ambiguous")
    {
        return @($State)
    }

    $States = @()
    $States += $State
    if (![string]::IsNullOrWhiteSpace([string]$State.Resolved) -and
        ![string]::Equals([string]$State.Resolved, [string]$State.Normalized, [System.StringComparison]::OrdinalIgnoreCase))
    {
        # Keep the literal path as a second comparable alias so a link and its
        # target both match the same editor.
        $States += @{ Normalized = [string]$State.Resolved; Resolved = [string]$State.Resolved; State = "exact" }
    }
    return $States
}

function Test-UeakShortNamePath
{
    param([AllowNull()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path))
    {
        return $false
    }
    return ([string]$Path) -match '~[0-9]'
}

function Compare-UeakProjectPath
{
    param(
        [AllowNull()][string]$Candidate,
        [Parameter(Mandatory = $true)][object[]]$TargetStates
    )

    $CandidateState = Resolve-UeakPathState -Path $Candidate
    if ($CandidateState.State -eq "invalid" -or $CandidateState.State -eq "ambiguous")
    {
        return "ambiguous"
    }

    $CandidateValues = @()
    if (![string]::IsNullOrWhiteSpace([string]$CandidateState.Normalized))
    {
        $CandidateValues += [string]$CandidateState.Normalized
    }
    if (![string]::IsNullOrWhiteSpace([string]$CandidateState.Resolved))
    {
        $CandidateValues += [string]$CandidateState.Resolved
    }
    if ($CandidateValues.Count -eq 0)
    {
        return "ambiguous"
    }

    foreach ($TargetState in @($TargetStates))
    {
        foreach ($TargetValue in @([string]$TargetState.Normalized, [string]$TargetState.Resolved))
        {
            if ([string]::IsNullOrWhiteSpace($TargetValue))
            {
                continue
            }
            foreach ($CandidateValue in $CandidateValues)
            {
                if ([string]::Equals($CandidateValue, $TargetValue, [System.StringComparison]::OrdinalIgnoreCase))
                {
                    return "same"
                }
            }
        }
    }

    # 8.3 short names and long names of the same file do not compare equal and
    # cannot be mapped without opening the file handle, so treat that pair as
    # unverifiable instead of guessing "different".
    foreach ($CandidateValue in $CandidateValues)
    {
        if (Test-UeakShortNamePath -Path $CandidateValue)
        {
            return "ambiguous"
        }
    }
    foreach ($TargetState in @($TargetStates))
    {
        if ((Test-UeakShortNamePath -Path ([string]$TargetState.Normalized)) -or
            (Test-UeakShortNamePath -Path ([string]$TargetState.Resolved)))
        {
            return "ambiguous"
        }
    }

    # A UNC path and a drive-letter path can name the same file through a
    # mapped drive or a DFS alias.  Drive mappings are per-user and are not
    # resolved here, so a mixed pair is unverifiable rather than "different".
    $CandidateIsUnc = $false
    foreach ($CandidateValue in $CandidateValues)
    {
        if ($CandidateValue.StartsWith('\\'))
        {
            $CandidateIsUnc = $true
        }
    }
    $TargetIsUnc = $false
    foreach ($TargetState in @($TargetStates))
    {
        if (([string]$TargetState.Normalized).StartsWith('\\'))
        {
            $TargetIsUnc = $true
        }
    }
    if ($CandidateIsUnc -ne $TargetIsUnc)
    {
        return "ambiguous"
    }

    # Both paths are absolute, link-free (or links we could resolve) and
    # different; a missing candidate has no link to hide a match.
    return "different"
}

function Get-UeakEditorProcessSnapshot
{
    $Processes = $null
    try
    {
        $Processes = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    }
    catch
    {
        throw "Unable to enumerate Win32_Process for the fixture concurrency guard: $($_.Exception.Message)"
    }

    $Snapshot = New-Object System.Collections.Generic.List[object]
    foreach ($Process in $Processes)
    {
        $Name = [string]$Process.Name
        if ($Name -notmatch $script:UeakEditorProcessPattern)
        {
            continue
        }

        $CommandLine = $null
        $CommandLineProperty = $Process.PSObject.Properties['CommandLine']
        if ($null -ne $CommandLineProperty -and $null -ne $CommandLineProperty.Value)
        {
            $CommandLine = [string]$CommandLineProperty.Value
        }

        $CreatedAt = ""
        $CreationProperty = $Process.PSObject.Properties['CreationDate']
        if ($null -ne $CreationProperty -and $null -ne $CreationProperty.Value)
        {
            try
            {
                $CreatedAt = ([datetime]$CreationProperty.Value).ToUniversalTime().ToString("o")
            }
            catch
            {
                $CreatedAt = ""
            }
        }

        $Snapshot.Add([pscustomobject]@{
            ProcessId = [int]$Process.ProcessId
            Name = $Name
            CreationDate = $CreatedAt
            CommandLine = $CommandLine
            HasCommandLine = !([string]::IsNullOrWhiteSpace($CommandLine))
        })
    }

    return $Snapshot.ToArray()
}

function New-UeakFixtureGuardDecision
{
    param(
        [bool]$Allowed,
        [string]$Code,
        [string]$Message,
        [AllowNull()][object[]]$Processes = @(),
        [AllowNull()][string[]]$MatchedProcessIds = @(),
        [AllowNull()][string[]]$AmbiguousProcessIds = @(),
        [AllowNull()][string[]]$DifferentProjectProcessIds = @(),
        [AllowNull()][string]$TargetProject = "",
        [AllowNull()][string]$Detail = ""
    )

    return [pscustomobject]@{
        Allowed = $Allowed
        Code = $Code
        Message = $Message
        Detail = $Detail
        TargetProject = $TargetProject
        Processes = @($Processes)
        MatchedProcessIds = @($MatchedProcessIds)
        AmbiguousProcessIds = @($AmbiguousProcessIds)
        DifferentProjectProcessIds = @($DifferentProjectProcessIds)
    }
}

function Get-UeakFixtureConcurrencyDecision
{
    param(
        [Parameter(Mandatory = $true)][string]$ProjectPath,
        # Test-only injection seam.  The production script never supplies a
        # snapshot, so there is no runtime switch that can skip the guard.
        [AllowNull()][object[]]$ProcessSnapshot = $null
    )

    $TargetStates = @(Get-UeakProjectPathStates -Path $ProjectPath)
    if ($TargetStates.Count -eq 0)
    {
        return New-UeakFixtureGuardDecision -Allowed $false -Code $script:UeakGuardCodeUnverifiable `
            -Message "The target project path is not an existing absolute .uproject path, so the fixture concurrency guard cannot decide." `
            -TargetProject $ProjectPath
    }
    foreach ($TargetState in $TargetStates)
    {
        if ($TargetState.State -eq "ambiguous" -or $TargetState.State -eq "invalid")
        {
            return New-UeakFixtureGuardDecision -Allowed $false -Code $script:UeakGuardCodeUnverifiable `
                -Message "The target project path resolves ambiguously; refusing to start a fixture commandlet." `
                -TargetProject $ProjectPath
        }
    }

    $Processes = @()
    if ($null -ne $ProcessSnapshot)
    {
        $Processes = @($ProcessSnapshot)
    }
    else
    {
        try
        {
            $Processes = @(Get-UeakEditorProcessSnapshot)
        }
        catch
        {
            return New-UeakFixtureGuardDecision -Allowed $false -Code $script:UeakGuardCodeUnverifiable `
                -Message "Cannot enumerate Unreal Editor processes ($($_.Exception.Message)); refusing to start a fixture commandlet." `
                -TargetProject $ProjectPath
        }
    }

    $Matched = New-Object System.Collections.Generic.List[string]
    $Ambiguous = New-Object System.Collections.Generic.List[string]
    $Different = New-Object System.Collections.Generic.List[string]
    $Records = New-Object System.Collections.Generic.List[object]

    foreach ($Process in $Processes)
    {
        $Name = ""
        $NameProperty = $Process.PSObject.Properties['Name']
        if ($null -ne $NameProperty -and $null -ne $NameProperty.Value)
        {
            $Name = [string]$NameProperty.Value
        }
        if ($Name -notmatch $script:UeakEditorProcessPattern)
        {
            continue
        }

        $ProcessId = ""
        $IdProperty = $Process.PSObject.Properties['ProcessId']
        if ($null -ne $IdProperty -and $null -ne $IdProperty.Value)
        {
            $ProcessId = [string]$IdProperty.Value
        }

        $CommandLine = $null
        $CommandLineProperty = $Process.PSObject.Properties['CommandLine']
        if ($null -ne $CommandLineProperty -and $null -ne $CommandLineProperty.Value)
        {
            $CommandLine = [string]$CommandLineProperty.Value
        }

        $CreatedAt = ""
        $CreationProperty = $Process.PSObject.Properties['CreationDate']
        if ($null -ne $CreationProperty -and $null -ne $CreationProperty.Value)
        {
            $CreatedAt = [string]$CreationProperty.Value
        }

        $Classification = "ambiguous"
        $ProjectArguments = @()
        if ([string]::IsNullOrWhiteSpace($CommandLine))
        {
            $Classification = "ambiguous"
        }
        else
        {
            $ProjectArguments = @(Get-UeakProjectArgumentsFromCommandLine -CommandLine $CommandLine)
            if ($ProjectArguments.Count -eq 0)
            {
                # An editor with no readable project argument cannot be tied to
                # another project, so it blocks rather than silently allowing.
                $Classification = "ambiguous"
            }
            else
            {
                $Classification = "different"
                $SawAmbiguous = $false
                foreach ($ProjectArgument in $ProjectArguments)
                {
                    $Comparison = Compare-UeakProjectPath -Candidate $ProjectArgument -TargetStates $TargetStates
                    if ($Comparison -eq "same")
                    {
                        $Classification = "same"
                        break
                    }
                    if ($Comparison -eq "ambiguous")
                    {
                        $SawAmbiguous = $true
                    }
                }
                if ($Classification -ne "same" -and $SawAmbiguous)
                {
                    $Classification = "ambiguous"
                }
            }
        }

        switch ($Classification)
        {
            "same" { $Matched.Add($ProcessId) }
            "ambiguous" { $Ambiguous.Add($ProcessId) }
            default { $Different.Add($ProcessId) }
        }

        $AuditCommandLine = ""
        if ($null -ne $CommandLine)
        {
            $AuditCommandLine = $CommandLine
            if ($AuditCommandLine.Length -gt 1024)
            {
                $AuditCommandLine = $AuditCommandLine.Substring(0, 1024)
            }
        }

        $Records.Add([pscustomobject]@{
            ProcessId = $ProcessId
            Name = $Name
            CreationDate = $CreatedAt
            Classification = $Classification
            ProjectArguments = @($ProjectArguments)
            CommandLine = $AuditCommandLine
        })
    }

    if ($Matched.Count -gt 0)
    {
        return New-UeakFixtureGuardDecision -Allowed $false -Code $script:UeakGuardCodeInUse `
            -Message ("Unreal Editor process(es) {0} already hold this exact project. Close that Editor, or target a different project, before writing fixtures." -f ($Matched -join ', ')) `
            -Processes $Records.ToArray() -MatchedProcessIds $Matched.ToArray() `
            -AmbiguousProcessIds $Ambiguous.ToArray() -DifferentProjectProcessIds $Different.ToArray() `
            -TargetProject $ProjectPath
    }

    if ($Ambiguous.Count -gt 0)
    {
        return New-UeakFixtureGuardDecision -Allowed $false -Code $script:UeakGuardCodeUnverifiable `
            -Message ("Unreal Editor process(es) {0} cannot be proven to use a different project (unreadable, relative or unresolvable command line). Close that Editor before writing fixtures." -f ($Ambiguous -join ', ')) `
            -Processes $Records.ToArray() -MatchedProcessIds $Matched.ToArray() `
            -AmbiguousProcessIds $Ambiguous.ToArray() -DifferentProjectProcessIds $Different.ToArray() `
            -TargetProject $ProjectPath
    }

    return New-UeakFixtureGuardDecision -Allowed $true -Code $script:UeakGuardCodeClear `
        -Message ("No Unreal Editor process holds this project ({0} other-project process(es) ignored)." -f $Different.Count) `
        -Processes $Records.ToArray() -MatchedProcessIds $Matched.ToArray() `
        -AmbiguousProcessIds $Ambiguous.ToArray() -DifferentProjectProcessIds $Different.ToArray() `
        -TargetProject $ProjectPath
}

function Write-UeakFixtureGuardReport
{
    param(
        [Parameter(Mandatory = $true)]$Decision,
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Stage = "write"
    )

    $Directory = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($Path))
    if (![string]::IsNullOrWhiteSpace($Directory))
    {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    }

    $DecisionText = "block"
    if ($Decision.Allowed)
    {
        $DecisionText = "allow"
    }

    $Payload = [ordered]@{
        schemaVersion = $script:UeakFixtureGuardSchemaVersion
        stage = $Stage
        generatedAtUtc = [System.DateTime]::UtcNow.ToString("o")
        targetProject = $Decision.TargetProject
        decision = $DecisionText
        code = $Decision.Code
        message = $Decision.Message
        checkedEditorProcessCount = @($Decision.Processes).Count
        matchedProcessIds = @($Decision.MatchedProcessIds)
        ambiguousProcessIds = @($Decision.AmbiguousProcessIds)
        differentProjectProcessIds = @($Decision.DifferentProjectProcessIds)
        processes = @($Decision.Processes)
    }

    $Json = $Payload | ConvertTo-Json -Depth 6
    Set-Content -LiteralPath $Path -Value $Json -Encoding UTF8
}

function Assert-UeakFixtureProjectIdle
{
    param(
        [Parameter(Mandatory = $true)][string]$ProjectPath,
        [string]$Stage = "write",
        [string]$GuardReportPath = "",
        [AllowNull()][object[]]$ProcessSnapshot = $null
    )

    $Decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $ProjectPath -ProcessSnapshot $ProcessSnapshot

    if (![string]::IsNullOrWhiteSpace($GuardReportPath))
    {
        try
        {
            Write-UeakFixtureGuardReport -Decision $Decision -Path $GuardReportPath -Stage $Stage
        }
        catch
        {
            # The audit trail is best-effort; it must never mask or bypass the
            # decision itself.
            Write-Warning "Could not write the fixture concurrency guard report: $($_.Exception.Message)"
        }
    }

    if (!$Decision.Allowed)
    {
        throw "$($Decision.Code): $($Decision.Message)"
    }

    return $Decision
}
