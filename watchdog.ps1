# Daily Cargo Bot - Watchdog (auto-restart on crash)
$workDir = "f:\daliy cargo"
Set-Location $workDir
$watchdogLog = "$workDir\watchdog.log"
$attempt = 0

while ($true) {
    $attempt++
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$ts] Starting bot (attempt #$attempt)" | Out-File $watchdogLog -Append -Encoding utf8

    $proc = Start-Process python -ArgumentList "-u", "bot.py" `
        -WorkingDirectory $workDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput "$workDir\bot.log" `
        -RedirectStandardError "$workDir\bot_err.log" `
        -PassThru
    $proc.Id | Out-File "$workDir\bot.pid" -Encoding ascii -NoNewline
    $proc.WaitForExit()

    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$ts] Bot exited (code $($proc.ExitCode)), restart in 15s" | Out-File $watchdogLog -Append -Encoding utf8
    Start-Sleep 15
}
