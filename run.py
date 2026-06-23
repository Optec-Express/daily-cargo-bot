"""
Watchdog for bot.py — restarts the bot if it crashes.
Run this instead of bot.py directly.
"""
import subprocess
import sys
import time
import os
from datetime import datetime
from pathlib import Path

# Windows: prevent subprocess from inheriting console handles
CREATE_NO_WINDOW = 0x08000000

WORK_DIR = Path(__file__).parent
LOG_FILE = WORK_DIR / "watchdog.log"
BOT_PID_FILE = WORK_DIR / "bot.pid"
RESTART_DELAY = 15  # seconds


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


attempt = 0
while True:
    attempt += 1
    log(f"Starting bot (attempt #{attempt})")
    try:
        bot_stdout = open(WORK_DIR / "bot.log", "w", encoding="utf-8")
        bot_stderr = open(WORK_DIR / "bot_err.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-u", "bot.py"],
            cwd=str(WORK_DIR),
            stdout=bot_stdout,
            stderr=bot_stderr,
            creationflags=CREATE_NO_WINDOW,
        )
        BOT_PID_FILE.write_text(str(proc.pid), encoding="ascii")
        proc.wait()
        log(f"Bot exited (code {proc.returncode}), restart in {RESTART_DELAY}s")
    except Exception as e:
        log(f"Error: {e}, restart in {RESTART_DELAY}s")
    time.sleep(RESTART_DELAY)
