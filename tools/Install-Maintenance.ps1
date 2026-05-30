<#
.SYNOPSIS
    Install-Maintenance.ps1 — настроить ежедневное обслуживание Memory Wiki
    через планировщик задач Windows (Task Scheduler).

.DESCRIPTION
    Создаёт задачу в Task Scheduler, которая запускает wiki-maintenance.py
    ежедневно в указанное время. Работает без Hermes.

.PARAMETER Time
    Время запуска в формате HH:mm (по умолч. 09:00).

.PARAMETER Uninstall
    Удалить задачу обслуживания.

.PARAMETER Status
    Показать статус задачи.

.EXAMPLE
    .\Install-Maintenance.ps1
    .\Install-Maintenance.ps1 -Time "06:00"
    .\Install-Maintenance.ps1 -Uninstall
    .\Install-Maintenance.ps1 -Status
#>

param(
    [string]$Time = "09:00",
    [switch]$Uninstall,
    [switch]$Status
)

$taskName = "Hermes Wiki Maintenance"
$scriptPath = Join-Path $PSScriptRoot "wiki-maintenance.py"
$python = "python"

# ── Status ────────────────────────────────────────────────────────────
if ($Status) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host "✅ Задача '$taskName' активна:" -ForegroundColor Green
        $task | Format-List TaskName, State, Triggers
    } else {
        Write-Host "❌ Задача '$taskName' не найдена" -ForegroundColor Red
    }
    return
}

# ── Uninstall ─────────────────────────────────────────────────────────
if ($Uninstall) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "✅ Задача '$taskName' удалена" -ForegroundColor Green
    } else {
        Write-Host "❌ Задача '$taskName' не найдена" -ForegroundColor Red
    }
    return
}

# ── Проверка ──────────────────────────────────────────────────────────
if (-not (Test-Path $scriptPath)) {
    Write-Host "❌ Скрипт не найден: $scriptPath" -ForegroundColor Red
    Write-Host "   Запусти из директории tools/ репозитория"
    exit 1
}

# ── Install ───────────────────────────────────────────────────────────
$action = New-ScheduledTaskAction -Execute "python" -Argument "`"$scriptPath`"" -WorkingDirectory (Split-Path $scriptPath -Parent)

$hour, $minute = $Time -split ":"
$trigger = New-ScheduledTaskTrigger -Daily -At "$hour`:$minute"

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force
    Write-Host "✅ Задача '$taskName' установлена на $Time ежедневно" -ForegroundColor Green
    Write-Host "   Путь: $scriptPath"
    Write-Host ""
    Write-Host "Проверка:" -ForegroundColor Cyan
    Get-ScheduledTask -TaskName $taskName | Format-List TaskName, State, Triggers
} catch {
    # Fallback: от имени текущего пользователя
    try {
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Force
        Write-Host "✅ Задача '$taskName' установлена (от пользователя)" -ForegroundColor Green
    } catch {
        Write-Host "❌ Ошибка: $_" -ForegroundColor Red
        Write-Host "   Попробуй запустить от Администратора"
    }
}
