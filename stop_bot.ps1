# Daily Cargo Bot - Stop Script
$workDir = "f:\daliy cargo"

# 先停 watchdog，防止它重启 bot
$watchdogPidFile = "$workDir\watchdog.pid"
if (Test-Path $watchdogPidFile) {
    $procId = [int](Get-Content $watchdogPidFile).Trim()
    try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Write-Host "Watchdog stopped (PID: $procId)"
    } catch {
        Write-Host "Watchdog $procId already stopped"
    }
    Remove-Item $watchdogPidFile -Force
}

# 再停 bot
$botPidFile = "$workDir\bot.pid"
if (Test-Path $botPidFile) {
    $procId = [int](Get-Content $botPidFile).Trim()
    try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Write-Host "Bot stopped (PID: $procId)"
    } catch {
        Write-Host "Bot process $procId not found, may already be stopped"
    }
    Remove-Item $botPidFile -Force
} else {
    Write-Host "No bot PID file, searching by process name..."
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like '*bot.py*' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force
            Write-Host "Killed bot process: $($_.ProcessId)"
        }
}
