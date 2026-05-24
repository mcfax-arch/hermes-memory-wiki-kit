param(
    [string]$MemoryRoot = $(if ($env:HERMES_MEMORY_ROOT) { $env:HERMES_MEMORY_ROOT } elseif ($env:AI_MEMORY_ROOT) { $env:AI_MEMORY_ROOT } else { Join-Path $HOME "Hermes_Memory" }),
    [string]$HermesHome = $(if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $HOME ".hermes" }),
    [switch]$InstallSkill,
    [switch]$AppendMemoryBootstrap
)

$ErrorActionPreference = "Stop"

$kitRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsSource = Join-Path $kitRoot "tools"
$templates = Join-Path $kitRoot "templates"

Write-Host "=== Hermes Memory Wiki Kit install ==="
Write-Host "Kit root   : $kitRoot"
Write-Host "Memory root: $MemoryRoot"
Write-Host "Hermes home: $HermesHome"

$dirs = @(
    $MemoryRoot,
    (Join-Path $MemoryRoot "knowledge-base"),
    (Join-Path $MemoryRoot "knowledge-base\raw"),
    (Join-Path $MemoryRoot "knowledge-base\raw\inbox"),
    (Join-Path $MemoryRoot "knowledge-base\raw\sources"),
    (Join-Path $MemoryRoot "knowledge-base\wiki"),
    (Join-Path $MemoryRoot "knowledge-base\wiki\captures"),
    (Join-Path $MemoryRoot "knowledge-base\wiki\concepts"),
    (Join-Path $MemoryRoot "knowledge-base\wiki\projects"),
    (Join-Path $MemoryRoot "knowledge-base\wiki\sources"),
    (Join-Path $MemoryRoot "knowledge-base\wiki\decisions"),
    (Join-Path $MemoryRoot "knowledge-base\wiki\maintenance"),
    (Join-Path $MemoryRoot "knowledge-base\wiki\principles"),
    (Join-Path $MemoryRoot "knowledge-base\wiki\tools"),
    (Join-Path $MemoryRoot "knowledge-base\state"),
    (Join-Path $MemoryRoot "knowledge-base\state\reports"),
    (Join-Path $MemoryRoot "knowledge-base\state\projects"),
    (Join-Path $MemoryRoot "knowledge-base\evals"),
    (Join-Path $MemoryRoot "knowledge-base\tools")
)

foreach ($dir in $dirs) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

Copy-Item -Path (Join-Path $toolsSource "*") -Destination (Join-Path $MemoryRoot "knowledge-base\tools") -Recurse -Force

$agentsTarget = Join-Path $MemoryRoot "AGENTS.md"
if (-not (Test-Path -LiteralPath $agentsTarget)) {
    (Get-Content -LiteralPath (Join-Path $kitRoot "AGENTS.md") -Raw).Replace("{MEMORY_ROOT}", $MemoryRoot) |
        Set-Content -LiteralPath $agentsTarget -Encoding UTF8
}

$hermesTarget = Join-Path $MemoryRoot "HERMES.md"
if (-not (Test-Path -LiteralPath $hermesTarget)) {
    (Get-Content -LiteralPath (Join-Path $kitRoot "HERMES.md") -Raw).Replace("{MEMORY_ROOT}", $MemoryRoot) |
        Set-Content -LiteralPath $hermesTarget -Encoding UTF8
}

$wikiFiles = @{
    "index.md" = "wiki-index.md"
    "log.md" = "wiki-log.md"
    "projects-map.md" = "projects-map.md"
    "open_questions.md" = "open_questions.md"
    "contradictions.md" = "contradictions.md"
}

foreach ($item in $wikiFiles.GetEnumerator()) {
    $target = Join-Path (Join-Path $MemoryRoot "knowledge-base\wiki") $item.Key
    if (-not (Test-Path -LiteralPath $target)) {
        (Get-Content -LiteralPath (Join-Path $templates $item.Value) -Raw).Replace("{MEMORY_ROOT}", $MemoryRoot) |
            Set-Content -LiteralPath $target -Encoding UTF8
    }
}

if ($InstallSkill) {
    $skillTarget = Join-Path $HermesHome "skills\memory-wiki"
    New-Item -ItemType Directory -Force -Path $skillTarget | Out-Null
    (Get-Content -LiteralPath (Join-Path $kitRoot "skills\memory-wiki\SKILL.md") -Raw).Replace("{MEMORY_ROOT}", $MemoryRoot) |
        Set-Content -LiteralPath (Join-Path $skillTarget "SKILL.md") -Encoding UTF8
    Write-Host "[OK] Skill installed: $skillTarget"
}

if ($AppendMemoryBootstrap) {
    $memDir = Join-Path $HermesHome "memories"
    $memFile = Join-Path $memDir "MEMORY.md"
    New-Item -ItemType Directory -Force -Path $memDir | Out-Null
    $bootstrap = (Get-Content -LiteralPath (Join-Path $templates "MEMORY_BOOTSTRAP.md") -Raw).Replace("{MEMORY_ROOT}", $MemoryRoot)
    $existing = if (Test-Path -LiteralPath $memFile) { Get-Content -LiteralPath $memFile -Raw } else { "" }
    if ($existing -notmatch [regex]::Escape("External long-term memory lives at")) {
        if ($existing.Trim()) {
            Add-Content -LiteralPath $memFile -Value "`n---`n$bootstrap" -Encoding UTF8
        } else {
            Set-Content -LiteralPath $memFile -Value $bootstrap -Encoding UTF8
        }
        Write-Host "[OK] MEMORY.md bootstrap appended: $memFile"
    } else {
        Write-Host "[SKIP] MEMORY.md already has external memory bootstrap"
    }
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "1. Add templates\MEMORY_BOOTSTRAP.md to $HermesHome\memories\MEMORY.md if not using -AppendMemoryBootstrap."
Write-Host "2. Copy skills\memory-wiki to $HermesHome\skills\memory-wiki if not using -InstallSkill."
Write-Host "3. Optional: paste mcp\config.yaml.snippet under $HermesHome\config.yaml."
Write-Host "4. Test:"
Write-Host "   python `"$MemoryRoot\knowledge-base\tools\memory-maintain.py`" --stats --memory-root `"$MemoryRoot`""
Write-Host "=== Done ==="

