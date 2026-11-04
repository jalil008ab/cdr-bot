#!/usr/bin/env bash
set -e

# Standart sozlamalar
export WORKER_URL="${WORKER_URL:-http://127.0.0.1:8081}"
export DB_PATH="${DB_PATH:-/tmp/bot.db}"
export JOB_MEM_MB="${JOB_MEM_MB:-350}"
export MAX_FILE_MB="${MAX_FILE_MB:-15}"

echo "[STARTUP] Worker fon jarayonida ishga tushirilmoqda..."
python -m worker.server &
WORKER_PID=$!

# Worker tayyor bo'lishini kutish (max 15 soniya)
for i in $(seq 1 15); do
    if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/healthz', timeout=2)" 2>/dev/null; then
        echo "[STARTUP] Worker muvaffaqiyatli tayyor bo'ldi (port 8081)."
        break
    fi
    sleep 1
done

echo "[STARTUP] Bot ishga tushirilmoqda (PORT: ${PORT:-8080})..."
exec python -m bot.main
