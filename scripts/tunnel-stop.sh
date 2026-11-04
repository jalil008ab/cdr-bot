#!/usr/bin/env bash
# CDR-Bot: Tunnel va konteynerlarni to'xtatish
# Foydalanish:  ./scripts/tunnel-stop.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TUNNEL_PID_FILE="$PROJECT_DIR/.tunnel.pid"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }

echo ""
echo -e "${RED}╔══════════════════════════════════════════╗${NC}"
echo -e "${RED}║     CDR-Bot — To'xtatish               ║${NC}"
echo -e "${RED}╚══════════════════════════════════════════╝${NC}"
echo ""

# 1. Docker konteynerlarni to'xtatish
info "Docker konteynerlar to'xtatilmoqda..."
cd "$PROJECT_DIR"
docker compose down 2>/dev/null || true
ok "Konteynerlar to'xtatildi"

# 2. Cloudflare tunnel va watchdog'ni to'xtatish
WATCHDOG_PID_FILE="$PROJECT_DIR/.watchdog.pid"
if [[ -f "$WATCHDOG_PID_FILE" ]]; then
    wpid=$(cat "$WATCHDOG_PID_FILE" 2>/dev/null || true)
    if [[ -n "$wpid" ]] && kill -0 "$wpid" 2>/dev/null; then
        kill "$wpid" 2>/dev/null || true
    fi
    rm -f "$WATCHDOG_PID_FILE"
fi
pkill -f "tunnel-watchdog.sh" 2>/dev/null || true

if [[ -f "$TUNNEL_PID_FILE" ]]; then
    pid=$(cat "$TUNNEL_PID_FILE" 2>/dev/null || true)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        info "Tunnel (PID $pid) to'xtatilmoqda..."
        kill "$pid" 2>/dev/null || true
        sleep 2
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$TUNNEL_PID_FILE"
fi

# Qolgan cloudflared jarayonlarini ham to'xtatish
pkill -f "cloudflared.*tunnel.*--url" 2>/dev/null || true

ok "Tunnel to'xtatildi"

# 3. Log faylni tozalash
rm -f "$PROJECT_DIR/.tunnel.log"

echo ""
ok "CDR-Bot to'liq to'xtatildi."
echo ""
