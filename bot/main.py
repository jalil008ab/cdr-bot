"""Kirish nuqtasi: aiohttp + aiogram webhook (HTTPS ortida), secret_token tekshiruvi bilan. Polling YO'Q."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
import sys
import urllib.parse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ErrorEvent
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from .config import ConfigError, Settings, load_settings
from .handlers import GuardMiddleware, build_router
from .logging_setup import setup_logging
from .storage import Database
from .worker_client import WorkerClient

log = logging.getLogger("bot")
PURGE_TASK = web.AppKey("purge_task", asyncio.Task)


def create_app(settings: Settings, *, register_webhook: bool = True) -> web.Application:
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=None, link_preview_is_disabled=True))
    db = Database(settings.db_path, settings.data_key)
    worker = WorkerClient(settings.worker_url, settings.worker_token, max_out_bytes=settings.max_file_bytes * 2)
    dp = Dispatcher()
    dp.message.outer_middleware(GuardMiddleware(settings, db))
    dp.include_router(build_router(settings, db, worker))

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        # Foydalanuvchiga faqat umumiy xabar; tafsilot faqat logga
        log.error("unhandled_error", exc_info=event.exception)
        msg = event.update.message
        if msg is not None:
            try:
                await msg.answer("Kechirasiz, xatolik yuz berdi. Keyinroq urinib ko'ring.")
            except Exception:  # noqa: BLE001
                log.debug("error_reply_failed", exc_info=True)
        return True

    async def on_startup(app: web.Application) -> None:
        if register_webhook:
            ip_addr: str | None = None
            try:
                host = urllib.parse.urlsplit(settings.webhook_base_url).hostname
                if host:
                    ip_addr = await asyncio.to_thread(socket.gethostbyname, host)
            except Exception:  # noqa: BLE001
                ip_addr = None

            for attempt in range(1, 6):
                try:
                    await bot.set_webhook(
                        url=settings.webhook_url,
                        secret_token=settings.webhook_secret,
                        allowed_updates=["message"],
                        drop_pending_updates=False,
                        max_connections=20,
                        ip_address=ip_addr,
                    )
                    log.info("webhook_set_ok", extra={"attempt": attempt, "has_ip": bool(ip_addr), "url": settings.webhook_url})
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 5:
                        log.error("set_webhook_failed_final", exc_info=exc)
                        raise
                    log.warning("set_webhook_retry", extra={"attempt": attempt, "error": str(exc)})
                    await asyncio.sleep(3)

        purged = await asyncio.to_thread(db.purge_old, settings.retention_days)
        app[PURGE_TASK] = asyncio.create_task(_purge_loop(db, settings.retention_days))
        log.info("startup", extra={"webhook_path": settings.webhook_path, "purged_jobs": purged})

    async def on_cleanup(app: web.Application) -> None:
        app[PURGE_TASK].cancel()
        await bot.session.close()
        db.close()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application(client_max_size=1_000_000)  # Telegram update'lari kichik
    app.router.add_get("/healthz", health)
    handler = SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.webhook_secret)
    handler.register(app, path="/webhook")
    legacy_path = "/webhook/" + hashlib.sha256(settings.webhook_secret.encode()).hexdigest()[:24]
    handler.register(app, path=legacy_path)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


async def _purge_loop(db: Database, days: int) -> None:
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            n = await asyncio.to_thread(db.purge_old, days)
            log.info("retention_purge", extra={"deleted_jobs": n})
        except Exception:  # noqa: BLE001
            log.exception("retention_purge_failed")


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Konfiguratsiya xatosi: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    setup_logging(settings.secrets())
    web.run_app(create_app(settings), host=settings.host, port=settings.port, access_log=None)


if __name__ == "__main__":
    main()
