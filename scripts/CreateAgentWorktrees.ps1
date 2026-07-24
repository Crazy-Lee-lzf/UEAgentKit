param(
    [string]$BaseCommit = "HEAD",
    [string]$WorktreeRoot = "",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$Repository = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
if ([string]::IsNullOrWhiteSpace($WorktreeRoot)) {
    $WorktreeRoot = Join-Path (Split-Path -Parent $Repository) "UEAgentKit-worktrees"
}

Push-Location $Repository
try {
    & git rev-parse --verify "$BaseCommit^{commit}" *> $null
    if ($LASTEXITCODE -ne 0) { throw "Base commit is invalid: $BaseCommit" }
    if ($Apply) {
        & git diff --quiet
        if ($LASTEXITCODE -ne 0) { throw "Working tree has unstaged changes." }
        & git diff --cached --quiet
        if ($LASTEXITCODE -ne 0) { throw "Working tree has staged changes." }
    }

    $Tasks = @(
        @{ Name = "navigation"; Branch = "feat/053-navigation" },
        @{ Name = "validation"; Branch = "feat/053-validation" },
        @{ Name = "protocol"; Branch = "feat/053-protocol" }
    )
    foreach ($Task in $Tasks) {
        $Path = Join-Path $WorktreeRoot $Task.Name
        Write-Host ("git worktree add -b {0} {1} {2}" -f $Task.Branch, $Path, $BaseCommit)
        if (-not $Apply) { continue }
        if (Test-Path -LiteralPath $Path) { throw "Worktree path already exists: $Path" }
        & git show-ref --verify --quiet ("refs/heads/{0}" -f $Task.Branch)
        if ($LASTEXITCODE -eq 0) { throw "Branch already exists: $($Task.Branch)" }
        New-Item -ItemType Directory -Force -Path $WorktreeRoot | Out-Null
        & git worktree add -b $Task.Branch $Path $BaseCommit
        if ($LASTEXITCODE -ne 0) { throw "Failed to create worktree: $Path" }
    }
    if (-not $Apply) {
        Write-Host "Preview only. Re-run with -Apply to create these branches and worktrees."
    }
}
finally {
    Pop-Location
}
