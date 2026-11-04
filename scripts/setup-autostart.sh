#!/usr/bin/env bash
# CDR-Bot: Mac uxlashini oldini olish va avtomatik ishga tushirishni sozlash
# Foydalanish:  ./scripts/setup-autostart.sh
#
# Bu skript:
#   1. Mac uxlashini oldini olish (caffeinate LaunchAgent)
#   2. CDR-Bot tunnel'ni login'da avtomatik ishga tushirish (LaunchAgent)
#   3. .gitignore'ga kerakli fayllarni qo'shish

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  CDR-Bot — Avtomatik ishga tushirish     ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

mkdir -p "$LAUNCH_AGENTS_DIR"

# ---------- 1. caffeinate LaunchAgent ----------
info "caffeinate LaunchAgent yaratilmoqda (Mac uxlashini oldini oladi)..."

cat > "$LAUNCH_AGENTS_DIR/com.cdr-bot.caffeinate.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cdr-bot.caffeinate</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-dims</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
PLIST

# Eski versiyani to'xtatish (agar bor bo'lsa)
launchctl bootout gui/$(id -u) "$LAUNCH_AGENTS_DIR/com.cdr-bot.caffeinate.plist" 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$LAUNCH_AGENTS_DIR/com.cdr-bot.caffeinate.plist"
ok "caffeinate LaunchAgent o'rnatildi va ishga tushdi"

# ---------- 2. CDR-Bot tunnel LaunchAgent ----------
info "CDR-Bot tunnel LaunchAgent yaratilmoqda..."

cat > "$LAUNCH_AGENTS_DIR/com.cdr-bot.tunnel.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cdr-bot.tunnel</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PROJECT_DIR}/scripts/tunnel-start.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/.launchagent-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/.launchagent-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>StartInterval</key>
    <integer>0</integer>
</dict>
</plist>
PLIST

ok "CDR-Bot tunnel LaunchAgent yaratildi"
warn "Bu LaunchAgent hozir YUKLANMADI — avval tunnel-start.sh ni qo'lda ishga tushiring va tekshiring."
warn "Hammasi ishlashini tasdiqlagach, quyidagi buyruqni bering:"
echo ""
echo -e "  ${YELLOW}launchctl bootstrap gui/\$(id -u) $LAUNCH_AGENTS_DIR/com.cdr-bot.tunnel.plist${NC}"
echo ""

# ---------- 3. .gitignore yangilash ----------
info ".gitignore yangilanmoqda..."

GITIGNORE="$PROJECT_DIR/.gitignore"
declare -a NEW_ENTRIES=(
    ".tunnel.log"
    ".tunnel.pid"
    ".launchagent-stdout.log"
    ".launchagent-stderr.log"
)

for entry in "${NEW_ENTRIES[@]}"; do
    if ! grep -qxF "$entry" "$GITIGNORE" 2>/dev/null; then
        echo "$entry" >> "$GITIGNORE"
        ok "  $entry qo'shildi"
    fi
done

# ---------- 4. Energy Saver sozlamasi ----------
echo ""
info "Energy Saver (Quvvat tejash) sozlamalari:"
warn "System Settings → Energy Saver (yoki Battery → Power Adapter) da:"
echo "  - 'Prevent automatic sleeping when the display is off' → ON"
echo "  - 'Wake for network access' → ON"
echo "  - 'Start up automatically after a power failure' → ON (agar mavjud bo'lsa)"
echo ""

ok "Sozlash tugadi!"
echo ""
