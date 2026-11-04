#!/usr/bin/env bash
# CDR-Bot: Cloudflare Tunnel orqali ishga tushirish skripti
# Bu skript:
#   1. Cloudflare quick tunnel'ni ishga tushiradi
#   2. Tunnel URL'ni ushlaydi
#   3. .env dagi WEBHOOK_BASE_URL'ni yangilaydi
#   4. Docker konteynerlarni ishga tushiradi (yoki qayta ishga tushiradi)
#   5. Webhook ishlayotganini tekshiradi
#
# Foydalanish:  ./scripts/tunnel-start.sh
# To'xtatish:   ./scripts/tunnel-stop.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
TUNNEL_LOG="$PROJECT_DIR/.tunnel.log"
TUNNEL_PID_FILE="$PROJECT_DIR/.tunnel.pid"

# ---------- ranglar ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[XATO]${NC}  $*" >&2; }

# ---------- oldingi tunnel'ni to'xtatish ----------
cleanup_old_tunnel() {
    if [[ -f "$TUNNEL_PID_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$TUNNEL_PID_FILE" 2>/dev/null || true)
        if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
            info "Eski tunnel (PID $old_pid) to'xtatilmoqda..."
            kill "$old_pid" 2>/dev/null || true
            sleep 2
            kill -9 "$old_pid" 2>/dev/null || true
        fi
        rm -f "$TUNNEL_PID_FILE"
    fi
    # Barcha cloudflared quick tunnel jarayonlarini ham tekshirish
    pkill -f "cloudflared.*tunnel.*--url" 2>/dev/null || true
    sleep 1
}

# ---------- .env mavjudligini tekshirish ----------
check_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        err ".env fayl topilmadi: $ENV_FILE"
        err "Avval:  cp .env.example .env  va qiymatlarni to'ldiring."
        exit 1
    fi
    # BOT_TOKEN borligini tekshirish (qiymatini ko'rsatmasdan!)
    if ! grep -q '^BOT_TOKEN=' "$ENV_FILE"; then
        err ".env da BOT_TOKEN topilmadi"
        exit 1
    fi
    if grep -q '^BOT_TOKEN=CHANGE_ME' "$ENV_FILE"; then
        err "BOT_TOKEN hali o'zgartirilmagan (.env ni to'ldiring)"
        exit 1
    fi
}

# ---------- tunnel ishga tushirish ----------
# Natija global o'zgaruvchiga yoziladi
TUNNEL_URL=""

start_tunnel() {
    info "Cloudflare Tunnel ishga tushirilmoqda..."

    # Log faylni tozalash
    : > "$TUNNEL_LOG"

    # Quick tunnel — bepul, ro'yxatdan o'tish shart emas
    nohup cloudflared tunnel --url http://localhost:8080 \
        --no-autoupdate \
        > "$TUNNEL_LOG" 2>&1 &
    local tunnel_pid=$!
    echo "$tunnel_pid" > "$TUNNEL_PID_FILE"

    info "Tunnel PID: $tunnel_pid — URL kutilmoqda..."

    # URL paydo bo'lishini kutish (max 30 soniya)
    TUNNEL_URL=""
    for i in $(seq 1 30); do
        TUNNEL_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
        if [[ -n "$TUNNEL_URL" ]]; then
            break
        fi
        # Jarayon to'xtab qolmaganini tekshirish
        if ! kill -0 "$tunnel_pid" 2>/dev/null; then
            err "cloudflared kutilmaganda to'xtadi. Log:"
            tail -20 "$TUNNEL_LOG" 2>/dev/null || true
            exit 1
        fi
        sleep 1
    done

    if [[ -z "$TUNNEL_URL" ]]; then
        err "30 soniya ichida tunnel URL olinmadi. Log:"
        tail -20 "$TUNNEL_LOG" 2>/dev/null || true
        kill "$tunnel_pid" 2>/dev/null || true
        exit 1
    fi

    ok "Tunnel URL: $TUNNEL_URL"
}

# ---------- .env dagi WEBHOOK_BASE_URL'ni yangilash ----------
update_env() {
    local new_url="$1"
    info "WEBHOOK_BASE_URL yangilanmoqda..."

    python3 -c '
import sys
env_path, target_url = sys.argv[1], sys.argv[2]
with open(env_path, "r", encoding="utf-8") as f:
    lines = f.readlines()
updated = False
out = []
for line in lines:
    if line.startswith("WEBHOOK_BASE_URL="):
        out.append(f"WEBHOOK_BASE_URL={target_url}\n")
        updated = True
    else:
        out.append(line)
if not updated:
    out.append(f"WEBHOOK_BASE_URL={target_url}\n")
with open(env_path, "w", encoding="utf-8") as f:
    f.writelines(out)
' "$ENV_FILE" "$new_url"

    ok "WEBHOOK_BASE_URL yangilandi"
}

# ---------- Docker konteynerlarni ishga tushirish ----------
start_containers() {
    info "Docker konteynerlar ishga tushirilmoqda..."
    cd "$PROJECT_DIR"

    # Konteynerlarni qayta build qilish va ishga tushirish
    docker compose up -d --build 2>&1 | while IFS= read -r line; do
        # Maxfiy ma'lumotlarni filtrlash
        echo "$line" | grep -v -E '(BOT_TOKEN|WEBHOOK_SECRET|WORKER_TOKEN|DATA_KEY)' || true
    done

    # Konteynerlar holatini kutish
    info "Konteynerlar tayyor bo'lishini kutish (max 60 soniya)..."
    for i in $(seq 1 60); do
        local bot_status worker_status
        bot_status=$(docker compose ps bot --format '{{.State}}' 2>/dev/null || echo "unknown")
        worker_status=$(docker compose ps worker --format '{{.State}}' 2>/dev/null || echo "unknown")

        if [[ "$bot_status" == "running" && "$worker_status" == "running" ]]; then
            ok "Ikkala konteyner ham ishlayapti"
            return 0
        fi

        # Bot tez tushib qolmaganini tekshirish
        if [[ "$bot_status" == "exited" && $i -gt 10 ]]; then
            err "Bot konteyneri tushib qoldi. Loglar:"
            docker compose logs bot --tail 20 2>&1 | grep -v -E '(BOT_TOKEN|WEBHOOK_SECRET|WORKER_TOKEN|DATA_KEY)' || true
            return 1
        fi

        sleep 1
    done

    warn "Konteynerlar 60 soniyada to'liq tayyor bo'lmadi"
    docker compose ps 2>/dev/null
    return 1
}

# ---------- webhook tekshiruvi ----------
verify_webhook() {
    local tunnel_url="$1"
    info "Webhook tekshirilmoqda..."

    # Bot tokenni .env dan o'qish (ekranga CHIQARMASDAN)
    local token
    token=$(grep '^BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)

    if [[ -z "$token" ]]; then
        warn "BOT_TOKEN o'qib bo'lmadi, webhook qo'lda tekshiring"
        return 0
    fi

    # Telegram API'dan webhook ma'lumotini olish
    local response
    response=$(curl -s "https://api.telegram.org/bot${token}/getWebhookInfo" 2>/dev/null || true)

    # Token'ni xotiradan tozalash
    unset token

    if [[ -z "$response" ]]; then
        warn "Telegram API'ga ulanib bo'lmadi (internet tekshiring)"
        return 0
    fi

    # URL o'rnatilganini tekshirish (to'liq URL'ni ko'rsatmasdan)
    if echo "$response" | grep -q '"url":"https://'; then
        local webhook_active
        webhook_active=$(echo "$response" | grep -o '"pending_update_count":[0-9]*' || echo "")
        ok "Webhook muvaffaqiyatli o'rnatilgan! $webhook_active"
        return 0
    else
        warn "Webhook hali o'rnatilmagan (bot konteyner ishga tushishini kuting)"
        return 0
    fi
}

# ================================================================
# ASOSIY OQIM
# ================================================================
main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║   CDR-Bot — Cloudflare Tunnel Starter   ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
    echo ""

    # 0. Tekshiruvlar
    check_env

    command -v cloudflared >/dev/null 2>&1 || { err "cloudflared o'rnatilmagan: brew install cloudflared"; exit 1; }
    command -v docker >/dev/null 2>&1      || { err "docker o'rnatilmagan"; exit 1; }
    docker info >/dev/null 2>&1            || { err "Docker Desktop ishlamayapti — uni ishga tushiring"; exit 1; }

    # 1. Eski tunnel'ni to'xtatish
    cleanup_old_tunnel

    # 2. Yangi tunnel ishga tushirish
    start_tunnel
    local tunnel_url="$TUNNEL_URL"

    # 3. .env yangilash
    update_env "$tunnel_url"

    # 4. Docker konteynerlarni ishga tushirish
    if ! start_containers; then
        err "Konteynerlar ishga tushmadi"
        exit 1
    fi

    # 5. Bir oz kutish — bot webhook'ni o'rnatsin
    info "Bot webhook'ni o'rnatishini kutish (5 soniya)..."
    sleep 5

    # 6. Webhook tekshiruvi
    verify_webhook "$tunnel_url"

    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║        ✅  CDR-Bot ishga tushdi!         ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  Tunnel URL:  ${BLUE}${tunnel_url}${NC}"
    echo -e "  Tunnel PID:  $(cat "$TUNNEL_PID_FILE")"
    echo -e "  Log:         $TUNNEL_LOG"
    echo ""
    echo -e "  ${YELLOW}To'xtatish:${NC}   ./scripts/tunnel-stop.sh"
    echo -e "  ${YELLOW}Loglar:${NC}       docker compose logs -f"
    echo ""
}

main "$@"
