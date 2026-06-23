# Daily Cargo Bot - Start Script
$workDir = "f:\daliy cargo"
$pythonw = "C:\Users\Lenovo\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
$watchdogPidFile = "$workDir\watchdog.pid"

if (Test-Path $watchdogPidFile) {
    $oldPid = [int](Get-Content $watchdogPidFile).Trim()
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
        Write-Host "Bot watchdog already running (PID: $oldPid)"
        exit 0
    }
    Remove-Item $watchdogPidFile -Force
}

$proc = Start-Process $pythonw `
    -ArgumentList "-u", "run.py" `
    -WorkingDirectory $workDir `
    -PassThru

$proc.Id | Out-File $watchdogPidFile -Encoding ascii -NoNewline
Write-Host "Bot watchdog started (PID: $($proc.Id))"
