param(
    [string]$MemoryRoot = $(if ($env:HERMES_MEMORY_ROOT) { $env:HERMES_MEMORY_ROOT } elseif ($env:AI_MEMORY_ROOT) { $env:AI_MEMORY_ROOT } else { Join-Path $HOME "Hermes_Memory" }),
    [string]$TaskName = "AI Memory Wiki Autopilot",
    [string]$At = "03:30",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$python = (Get-Command python).Source
$script = Join-Path $MemoryRoot "knowledge-base\tools\memory-autopilot.py"

if (-not (Test-Path -LiteralPath $script)) {
    throw "memory-autopilot.py not found: $script"
}

$arguments = "`"$script`" --memory-root `"$MemoryRoot`""
if ($DryRun) {
    $arguments += " --dry-run"
}

$action = New-ScheduledTaskAction -Execute $python -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Runs local memory-wiki graph, health, recall eval, and safe maintenance." -Force | Out-Null

Write-Host "=== Memory autopilot scheduled ==="
Write-Host "TaskName : $TaskName"
Write-Host "Time     : $At"
Write-Host "Python   : $python"
Write-Host "Script   : $script"
Write-Host "DryRun   : $DryRun"
Write-Host "=== Done ==="

