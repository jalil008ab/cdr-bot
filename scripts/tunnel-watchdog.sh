#!/usr/bin/env bash
# CDR-Bot: Cloudflare Tunnel nazoratchisi (Watchdog)
# Quick tunnel uzilib qolsa (masalan Cloudflare 530 yoki Tunnel not found bersa),
# bu skript avtomatik tarzda tunnel'ni qayta tiklaydi.
#
# Foydalanish:
#   ./scripts/tunnel-watchdog.sh &

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TUNNEL_LOG="$PROJECT_DIR/.tunnel.log"
PID_FILE="$PROJECT_DIR/.watchdog.pid"

echo "$$" > "$PID_FILE"

echo "[WATCHDOG] CDR-Bot tunnel nazoratchisi ishga tushdi (PID: $$)"

while true; do
    sleep 25

    # 1. .tunnel.pid borligini va jarayon tirikligini tekshirish
    tunnel_dead=0
    if [[ -f "$PROJECT_DIR/.tunnel.pid" ]]; then
        tpid=$(cat "$PROJECT_DIR/.tunnel.pid" 2>/dev/null || true)
        if [[ -z "$tpid" ]] || ! kill -0 "$tpid" 2>/dev/null; then
            tunnel_dead=1
        fi
    else
        tunnel_dead=1
    fi

    # 2. Logda "Unauthorized: Tunnel not found" borligini tekshirish
    tunnel_dropped=0
    if [[ -f "$TUNNEL_LOG" ]] && tail -15 "$TUNNEL_LOG" 2>/dev/null | grep -q "Tunnel not found"; then
        tunnel_dropped=1
    fi

    # 3. Agar tunnel to'xtagan yoki Cloudflare tomonida uzilgan bo'lsa:
    if [[ $tunnel_dead -eq 1 || $tunnel_dropped -eq 1 ]]; then
        echo "[WATCHDOG] $(date): Tunnel uzilishi aniqlandi! Qayta tiklanmoqda..."
        bash "$PROJECT_DIR/scripts/tunnel-start.sh" >> "$PROJECT_DIR/.watchdog.log" 2>&1 || true
        echo "[WATCHDOG] $(date): Qayta tiklandi."
        sleep 10
    fi
done
