# Offline functional tests for scripts/FixtureConcurrencyGuard.ps1 (DEF-07).
#
# No UE process is started or inspected: every case injects a synthetic
# Win32_Process-like snapshot, so this harness is deterministic and safe.
# Run: pwsh -NoProfile -File tests/powershell/FixtureConcurrencyGuard.Tests.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repository = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
. (Join-Path $repository "scripts\FixtureConcurrencyGuard.ps1")

$script:Passed = 0
$script:Failed = 0
$script:Skipped = 0

function Assert-Equal
{
    param($Expected, $Actual, [string]$Name)
    if ($Expected -eq $Actual)
    {
        $script:Passed++
        Write-Host ("  ok   {0}" -f $Name)
    }
    else
    {
        $script:Failed++
        Write-Host ("  FAIL {0} (expected '{1}', actual '{2}')" -f $Name, $Expected, $Actual) -ForegroundColor Red
    }
}

function Assert-True
{
    param([bool]$Condition, [string]$Name)
    Assert-Equal $true $Condition $Name
}

function New-FakeProcess
{
    param(
        [string]$Name = "UnrealEditor.exe",
        [AllowNull()][string]$CommandLine = "",
        [int]$ProcessId = 1000,
        [string]$CreationDate = "2026-11-03T00:00:00.0000000Z"
    )
    return [pscustomobject]@{
        ProcessId = $ProcessId
        Name = $Name
        CreationDate = $CreationDate
        CommandLine = $CommandLine
        HasCommandLine = !([string]::IsNullOrWhiteSpace($CommandLine))
    }
}

# --- disposable project layout -------------------------------------------
$root = Join-Path $env:TEMP ("ueak-def07-" + [System.Guid]::NewGuid().ToString("N"))
$spaced = Join-Path $root "With Space"
$other = Join-Path $root "Other"
$sameName = Join-Path $root "SameName"
New-Item -ItemType Directory -Path $spaced -Force | Out-Null
New-Item -ItemType Directory -Path $other -Force | Out-Null
New-Item -ItemType Directory -Path $sameName -Force | Out-Null

$target = Join-Path $spaced "Target.uproject"
$otherProject = Join-Path $other "Other.uproject"
$sameNameProject = Join-Path $sameName "Target.uproject"
$prefixProject = Join-Path $other "Target2.uproject"
foreach ($file in @($target, $otherProject, $sameNameProject, $prefixProject))
{
    New-Item -ItemType File -Path $file -Force | Out-Null
}

try
{
    Write-Host "`n[tokenizer]"
    $tokens = @(Split-UeakCommandLineArguments -CommandLine ('exe "C:\a b\c.uproject" -unattended'))
    Assert-Equal 3 @($tokens).Count "quoted argument stays one token"
    Assert-Equal 'C:\a b\c.uproject' $tokens[1] "quoted argument loses only its quotes"

    Write-Host "`n[decision: allow]"
    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @()
    Assert-Equal "fixture-project-lock-clear" $decision.Code "no editor allows"
    Assert-True $decision.Allowed "no editor Allowed=true"

    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $otherProject) -ProcessId 2001)
    )
    Assert-Equal "fixture-project-lock-clear" $decision.Code "another project allows"

    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $sameNameProject) -ProcessId 2002)
    )
    Assert-Equal "fixture-project-lock-clear" $decision.Code "same file name in another directory allows"

    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $prefixProject) -ProcessId 2003)
    )
    Assert-Equal "fixture-project-lock-clear" $decision.Code "name prefix does not match as a substring"

    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -Name "UnrealPak.exe" -CommandLine ('UnrealPak.exe "{0}"' -f $target) -ProcessId 2004)
    )
    Assert-Equal "fixture-project-lock-clear" $decision.Code "non-editor process is ignored"

    Write-Host "`n[decision: block same project]"
    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $target) -ProcessId 3001)
    )
    Assert-Equal "fixture-project-in-use" $decision.Code "exact project blocks"
    Assert-Equal "3001" (@($decision.MatchedProcessIds) -join ',') "matched pid recorded"

    $upper = $target.ToUpperInvariant()
    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $upper) -ProcessId 3002)
    )
    Assert-Equal "fixture-project-in-use" $decision.Code "different case blocks"

    $forwardSlash = $target -replace '\\', '/'
    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $forwardSlash) -ProcessId 3003)
    )
    Assert-Equal "fixture-project-in-use" $decision.Code "forward slashes block"

    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine ('UnrealEditor.exe -project="{0}"' -f $target) -ProcessId 3004)
    )
    Assert-Equal "fixture-project-in-use" $decision.Code "-project= form blocks"

    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine ('UnrealEditor.exe -project "{0}"' -f $target) -ProcessId 3005)
    )
    Assert-Equal "fixture-project-in-use" $decision.Code "-project space form blocks"

    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $otherProject) -ProcessId 3006),
        (New-FakeProcess -CommandLine ('UnrealEditor-Cmd.exe "{0}"' -f $target) -ProcessId 3007)
    )
    Assert-Equal "fixture-project-in-use" $decision.Code "mixed editors block"
    Assert-Equal "3006" (@($decision.DifferentProjectProcessIds) -join ',') "other project listed as different"

    Write-Host "`n[decision: fail closed]"
    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine "" -ProcessId 4001)
    )
    Assert-Equal "fixture-project-lock-unverifiable" $decision.Code "unreadable command line blocks"

    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine 'UnrealEditor.exe -unattended' -ProcessId 4002)
    )
    Assert-Equal "fixture-project-lock-unverifiable" $decision.Code "editor without a project argument blocks"

    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
        (New-FakeProcess -CommandLine 'UnrealEditor.exe ".\Target.uproject"' -ProcessId 4003)
    )
    Assert-Equal "fixture-project-lock-unverifiable" $decision.Code "relative project path blocks"

    $missingTarget = Join-Path $root "Missing\Nope.uproject"
    $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $missingTarget -ProcessSnapshot @()
    Assert-Equal "fixture-project-lock-unverifiable" $decision.Code "missing target project blocks"

    Write-Host "`n[decision: symlinked project]"
    $link = Join-Path $root "Linked.uproject"
    $linkCreated = $false
    try
    {
        New-Item -ItemType SymbolicLink -Path $link -Target $target -ErrorAction Stop | Out-Null
        $linkCreated = $true
    }
    catch
    {
        $script:Skipped++
        Write-Host ("  skip symlink case: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    }
    if ($linkCreated)
    {
        $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $link -ProcessSnapshot @(
            (New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $target) -ProcessId 5001)
        )
        Assert-Equal "fixture-project-in-use" $decision.Code "link target matches the real path"

        $decision = Get-UeakFixtureConcurrencyDecision -ProjectPath $target -ProcessSnapshot @(
            (New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $link) -ProcessId 5002)
        )
        Assert-Equal "fixture-project-in-use" $decision.Code "real target matches the link path"
    }

    Write-Host "`n[path normalization]"
    $unc = Resolve-UeakAbsolutePath -Path '\\server\share\Project\P.uproject'
    Assert-Equal '\\server\share\Project\P.uproject' $unc "UNC path is preserved"
    $trailing = Resolve-UeakAbsolutePath -Path ((Join-Path $spaced "Target.uproject") + '\')
    Assert-Equal $target $trailing "trailing separator is trimmed"
    $relative = Resolve-UeakAbsolutePath -Path '.\Target.uproject'
    Assert-Equal $true ($null -eq $relative) "relative path is not normalized"

    Write-Host "`n[UNC / mapped drive ambiguity]"
    $uncTargetStates = @(@{ Normalized = '\\server\share\Project\P.uproject'; Resolved = $null; State = "exact" })
    $driveCandidate = Join-Path $other "Other.uproject"
    Assert-Equal "ambiguous" (Compare-UeakProjectPath -Candidate $driveCandidate -TargetStates $uncTargetStates) `
        "a drive path cannot be proven different from a UNC path"
    Assert-Equal "different" (Compare-UeakProjectPath -Candidate '\\server\share2\Project\P.uproject' -TargetStates $uncTargetStates) `
        "two UNC paths on different shares are different"

    Write-Host "`n[assert helper]"
    $reportPath = Join-Path $root "guard-report.json"
    $threw = $false
    try
    {
        Assert-UeakFixtureProjectIdle -ProjectPath $target -Stage "write" -GuardReportPath $reportPath `
            -ProcessSnapshot @((New-FakeProcess -CommandLine ('UnrealEditor.exe "{0}"' -f $target) -ProcessId 6001)) | Out-Null
    }
    catch
    {
        $threw = $true
        Assert-True ($_.Exception.Message -like "fixture-project-in-use:*") "blocking error carries the code"
    }
    Assert-True $threw "assert helper throws when blocked"
    Assert-True (Test-Path -LiteralPath $reportPath) "blocked decision still writes the audit report"
    if (Test-Path -LiteralPath $reportPath)
    {
        $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
        Assert-Equal "block" ([string]$report.decision) "audit report records the block"
        Assert-Equal "write" ([string]$report.stage) "audit report records the stage"
        Assert-Equal "fixture-project-in-use" ([string]$report.code) "audit report records the code"
    }

    $allowReport = Join-Path $root "guard-allow.json"
    $decision = Assert-UeakFixtureProjectIdle -ProjectPath $target -Stage "entry" -GuardReportPath $allowReport `
        -ProcessSnapshot @()
    Assert-True $decision.Allowed "assert helper returns the decision when clear"
    $allow = Get-Content -LiteralPath $allowReport -Raw | ConvertFrom-Json
    Assert-Equal "allow" ([string]$allow.decision) "audit report records the allow"
}
finally
{
    if (Test-Path -LiteralPath $root)
    {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ("`n{0} passed, {1} failed, {2} skipped" -f $script:Passed, $script:Failed, $script:Skipped)
if ($script:Failed -gt 0)
{
    exit 1
}
exit 0
