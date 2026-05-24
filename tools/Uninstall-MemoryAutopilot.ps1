param(
    [string]$TaskName = "AI Memory Wiki Autopilot"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "=== Memory autopilot removed ==="
    Write-Host "TaskName : $TaskName"
    Write-Host "=== Done ==="
} else {
    Write-Host "=== Memory autopilot not found ==="
    Write-Host "TaskName : $TaskName"
    Write-Host "=== Done ==="
}

