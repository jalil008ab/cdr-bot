"""Kirish nuqtasi: aiohttp + aiogram webhook (HTTPS ortida), secret_token tekshiruvi bilan. Polling YO'Q."""
from __future__ import annotations

import asyncio
import logging
import sys

import aiohttp
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


async def _wait_for_worker(worker_url: str, retries: int = 15, delay: float = 2.0) -> None:
    """Worker tayyor bo'lguncha kutadi (bitta konteynerda supervisord bilan kerak)."""
    health_url = worker_url + "/healthz"
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 200:
                        log.info("worker_ready", extra={"attempt": attempt + 1})
                        return
        except Exception:  # noqa: BLE001
            pass
        log.info("waiting_for_worker", extra={"attempt": attempt + 1, "delay": delay})
        await asyncio.sleep(delay)
    raise RuntimeError(f"Worker {worker_url} {retries} urinishdan keyin ham tayyor bo'lmadi")


def create_app(settings: Settings, *, register_webhook: bool = True) -> web.Application:
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=None, link_preview_is_disabled=True))
    db = Database(settings.db_path, settings.data_key)
    worker = WorkerClient(settings.worker_url, settings.worker_token, max_out_bytes=settings.max_file_bytes * 2)
    dp = Dispatcher()
    dp.message.outer_middleware(GuardMiddleware(settings, db))
    dp.include_router(build_router(settings, db, worker))

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.error("unhandled_error", exc_info=event.exception)
        msg = event.update.message
        if msg is not None:
            try:
                await msg.answer("Kechirasiz, xatolik yuz berdi. Keyinroq urinib ko'ring.")
            except Exception:  # noqa: BLE001
                log.debug("error_reply_failed", exc_info=True)
        return True

    async def on_startup(app: web.Application) -> None:
        # Worker tayyor bo'lishini kutamiz (bitta konteynerda supervisord bilan ishlaydi)
        await _wait_for_worker(settings.worker_url)
        if register_webhook:
            await bot.set_webhook(
                url=settings.webhook_url,
                secret_token=settings.webhook_secret,
                allowed_updates=["message"],
                drop_pending_updates=True,
                max_connections=20,
            )
        purged = await asyncio.to_thread(db.purge_old, settings.retention_days)
        app[PURGE_TASK] = asyncio.create_task(_purge_loop(db, settings.retention_days))
        log.info("startup", extra={"webhook_path": "/webhook/***", "purged_jobs": purged})

    async def on_cleanup(app: web.Application) -> None:
        app[PURGE_TASK].cancel()
        await bot.session.close()
        db.close()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application(client_max_size=1_000_000)
    app.router.add_get("/healthz", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.webhook_secret).register(app, path=settings.webhook_path)
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
