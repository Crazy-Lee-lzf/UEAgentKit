param(
    [ValidateSet("Registry", "Query", "Live", "Workflow", "All")]
    [string]$Group = "All"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}
$env:PYTHONPATH = ((Join-Path $Root "src") + [IO.Path]::PathSeparator + $env:PYTHONPATH)

Push-Location (Join-Path $Root "tests\python")
try {
    switch ($Group) {
        "Registry" { & $Python -m unittest test_tool_registry }
        "Query" { & $Python -m unittest test_indexer_queries test_mcp_server }
        "Live" { & $Python -m unittest test_editor_bridge test_mcp_server }
        "Workflow" { & $Python -m unittest test_agent_workflow test_snapshot_lifecycle test_mcp_server }
        default { & $Python -m unittest discover -s . -p "test_*.py" }
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
