"""Sozlamalar faqat muhit o'zgaruvchilaridan (.env) o'qiladi. Sir (secret)lar kodda yoki gitda bo'lmaydi."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Mapping

from cryptography.fernet import Fernet


class ConfigError(Exception):
    pass


_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    webhook_base_url: str
    webhook_secret: str
    worker_url: str
    worker_token: str
    data_key: bytes
    admin_ids: frozenset[int]
    db_path: str = "/data/bot.db"
    # Konteyner ichida hamma interfeysda; tashqariga faqat 127.0.0.1 port-publish yoki proksi orqali chiqadi
    host: str = "0.0.0.0"  # nosec B104
    port: int = 8080
    max_file_bytes: int = 20 * 1024 * 1024
    msg_limit: int = 20
    msg_window: int = 60
    file_limit: int = 5
    file_window: int = 600
    retention_days: int = 30

    @property
    def webhook_path(self) -> str:
        return "/webhook"

    @property
    def webhook_url(self) -> str:
        return self.webhook_base_url + self.webhook_path

    def secrets(self) -> list[str]:
        return [self.bot_token, self.webhook_secret, self.worker_token, self.data_key.decode()]


def _int(env: Mapping[str, str], key: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        val = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} butun son bo'lishi kerak") from exc
    if not lo <= val <= hi:
        raise ConfigError(f"{key} {lo}..{hi} oralig'ida bo'lishi kerak")
    return val


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def need(key: str) -> str:
        val = env.get(key, "").strip()
        if not val:
            raise ConfigError(f"{key} o'rnatilmagan (.env ni tekshiring)")
        return val

    token = need("BOT_TOKEN")
    if not _TOKEN_RE.match(token):
        raise ConfigError("BOT_TOKEN formati noto'g'ri")

    # Render.com avtomatik ravishda RENDER_EXTERNAL_URL beradi (masalan: https://cdr-bot-4tz4.onrender.com)
    base = (env.get("RENDER_EXTERNAL_URL") or env.get("WEBHOOK_BASE_URL", "")).strip().rstrip("/")
    if not base or not base.startswith("https://"):
        raise ConfigError("WEBHOOK_BASE_URL (yoki RENDER_EXTERNAL_URL) https:// bilan boshlanishi shart (polling taqiqlangan)")

    secret = need("WEBHOOK_SECRET")
    if not _SECRET_RE.match(secret):
        raise ConfigError("WEBHOOK_SECRET 32-256 belgi, faqat A-Z a-z 0-9 _ - bo'lishi kerak")

    worker_token = need("WORKER_TOKEN")
    if len(worker_token) < 32:
        raise ConfigError("WORKER_TOKEN kamida 32 belgi bo'lishi kerak")

    key = need("DATA_KEY").encode()
    try:
        Fernet(key)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError("DATA_KEY yaroqli Fernet kaliti emas (scripts/gen_secrets.py)") from exc

    admins: set[int] = set()
    for part in env.get("ADMIN_IDS", "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ConfigError("ADMIN_IDS faqat raqamli Telegram user ID'lar ro'yxati bo'lishi kerak (username emas)")
        admins.add(int(part))
    if not admins:
        raise ConfigError("ADMIN_IDS bo'sh: kamida bitta admin user ID kerak")

    return Settings(
        bot_token=token,
        webhook_base_url=base,
        webhook_secret=secret,
        worker_url=env.get("WORKER_URL", "http://worker:8081").rstrip("/"),
        worker_token=worker_token,
        data_key=key,
        admin_ids=frozenset(admins),
        db_path=env.get("DB_PATH", "/data/bot.db"),
        port=_int(env, "PORT", 8080, 1, 65535),
        max_file_bytes=_int(env, "MAX_FILE_MB", 20, 1, 20) * 1024 * 1024,
        msg_limit=_int(env, "MSG_LIMIT", 20, 1, 1000),
        msg_window=_int(env, "MSG_WINDOW", 60, 1, 3600),
        file_limit=_int(env, "FILE_LIMIT", 5, 1, 100),
        file_window=_int(env, "FILE_WINDOW", 600, 1, 86400),
        retention_days=_int(env, "RETENTION_DAYS", 30, 1, 3650),
    )
