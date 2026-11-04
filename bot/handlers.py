"""Telegram handlerlari va guard middleware."""
from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BufferedInputFile, Message, TelegramObject

from . import texts
from .config import Settings
from .security import SlidingWindowLimiter, is_allowed_ext, output_filename, split_ext
from .storage import Database
from .worker_client import WorkerBusy, WorkerClient, WorkerError, WorkerRejected

log = logging.getLogger("bot")


class GuardMiddleware:
    """Har bir xabardan oldin: botlarni rad etish, ban tekshiruvi, per-user rate limit, loglash."""

    def __init__(self, settings: Settings, db: Database):
        self.settings, self.db = settings, db
        self.msg_limiter = SlidingWindowLimiter(settings.msg_limit, settings.msg_window)
        self.notice_limiter = SlidingWindowLimiter(1, 30)  # "juda tez" javobini spam qilmaslik uchun

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        if not isinstance(event, Message) or event.from_user is None or event.from_user.is_bot:
            return None
        if event.chat.type != "private":  # faqat shaxsiy chat
            return None
        uid = event.from_user.id
        if uid not in self.settings.admin_ids and await asyncio.to_thread(self.db.is_banned, uid):
            log.warning("banned_user_blocked", extra={"user_id": uid})
            return None
        wait = self.msg_limiter.hit(uid)
        if wait:
            log.warning("rate_limited", extra={"user_id": uid, "kind": "message", "retry_after": round(wait, 1)})
            if not self.notice_limiter.hit(uid):
                await event.answer(f"Juda tez yuboryapsiz. {int(wait) + 1} soniyadan keyin urinib ko'ring.")
            return None
        await asyncio.to_thread(self.db.touch_user, uid)
        return await handler(event, data)


class FileGate:
    """Fayllar uchun alohida limit + bitta foydalanuvchiga bitta parallel ish."""

    def __init__(self, settings: Settings):
        self.limiter = SlidingWindowLimiter(settings.file_limit, settings.file_window)
        self.active: set[int] = set()
        self.global_sem = asyncio.Semaphore(4)


def build_router(settings: Settings, db: Database, worker: WorkerClient) -> Router:
    gate = FileGate(settings)
    r = Router()

    def is_admin(uid: int) -> bool:
        return uid in settings.admin_ids  # faqat user ID, hech qachon username emas

    @r.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(texts.START)

    @r.message(Command("help"))
    async def help_(message: Message) -> None:
        await message.answer(texts.HELP)

    @r.message(Command("policy"))
    async def policy(message: Message) -> None:
        await message.answer(texts.POLICY)

    # ------------------------------------------------------------------ admin buyruqlari
    async def deny_admin(message: Message, cmd: str) -> None:
        uid = message.from_user.id
        log.warning("admin_denied", extra={"user_id": uid, "command": cmd})
        await asyncio.to_thread(db.audit, uid, "admin_denied", cmd, "")
        await message.answer(texts.UNKNOWN_CMD)  # admin buyruqlari borligini oshkor qilmaymiz

    @r.message(Command("stats"))
    async def stats(message: Message) -> None:
        uid = message.from_user.id
        if not is_admin(uid):
            return await deny_admin(message, "stats")
        s = await asyncio.to_thread(db.stats)
        await asyncio.to_thread(db.audit, uid, "stats", "", "")
        await message.answer(
            f"Foydalanuvchilar: {s['users']} (banlangan: {s['banned']})\n"
            f"Ishlar jami: {s['jobs']}\n24 soatda: {s['ok_24h']} muvaffaqiyatli, {s['failed_24h']} muvaffaqiyatsiz\n"
            f"Jami zararsizlantirilgan elementlar: {s['neutralized_total']}"
        )

    def parse_target(command: CommandObject) -> int | None:
        arg = (command.args or "").strip()
        if not arg.isdigit() or len(arg) > 15:
            return None
        return int(arg)

    @r.message(Command("ban"))
    async def ban(message: Message, command: CommandObject) -> None:
        uid = message.from_user.id
        if not is_admin(uid):
            return await deny_admin(message, "ban")
        target = parse_target(command)
        if target is None:
            return await message.answer("Foydalanish: /ban <user_id> (faqat raqam)")
        if is_admin(target):
            return await message.answer("Adminni ban qilib bo'lmaydi.")
        await asyncio.to_thread(db.set_banned, target, True)
        await asyncio.to_thread(db.audit, uid, "ban", str(target), "")
        log.info("admin_action", extra={"user_id": uid, "action": "ban", "target": target})
        await message.answer(f"{target} banlandi.")

    @r.message(Command("unban"))
    async def unban(message: Message, command: CommandObject) -> None:
        uid = message.from_user.id
        if not is_admin(uid):
            return await deny_admin(message, "unban")
        target = parse_target(command)
        if target is None:
            return await message.answer("Foydalanish: /unban <user_id> (faqat raqam)")
        await asyncio.to_thread(db.set_banned, target, False)
        await asyncio.to_thread(db.audit, uid, "unban", str(target), "")
        log.info("admin_action", extra={"user_id": uid, "action": "unban", "target": target})
        await message.answer(f"{target} bandan chiqarildi.")

    @r.message(Command("audit"))
    async def audit(message: Message) -> None:
        uid = message.from_user.id
        if not is_admin(uid):
            return await deny_admin(message, "audit")
        rows = await asyncio.to_thread(db.recent_audit, 10)
        await asyncio.to_thread(db.audit, uid, "audit_view", "", "")
        if not rows:
            return await message.answer("Audit log bo'sh.")
        lines = [
            f"#{x['id']} {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(x['ts']))} UTC actor={x['actor_id']} {texts.safe_text(x['action'], 30)} {texts.safe_text(x['target'], 30)}"
            for x in rows
        ]
        await message.answer("\n".join(lines))

    @r.message(Command("verifyaudit"))
    async def verify_audit(message: Message) -> None:
        uid = message.from_user.id
        if not is_admin(uid):
            return await deny_admin(message, "verifyaudit")
        ok, bad = await asyncio.to_thread(db.verify_audit_chain)
        await asyncio.to_thread(db.audit, uid, "audit_verify", "", "ok" if ok else f"broken@{bad}")
        await message.answer("Audit zanjiri butun." if ok else f"DIQQAT: audit zanjiri #{bad} yozuvda buzilgan!")

    # ------------------------------------------------------------------ fayl qabul qilish
    @r.message(F.document)
    async def on_document(message: Message) -> None:
        uid = message.from_user.id
        doc = message.document
        stem, ext = split_ext(doc.file_name)

        if not is_allowed_ext(ext):
            log.warning("invalid_file", extra={"user_id": uid, "reason": "extension", "ext": ext[:10]})
            return await message.answer(texts.BAD_EXT)
        if doc.file_size is None or doc.file_size <= 0 or doc.file_size > settings.max_file_bytes:
            log.warning("invalid_file", extra={"user_id": uid, "reason": "size", "size": doc.file_size})
            return await message.answer(texts.TOO_BIG)
        if uid in gate.active:
            return await message.answer(texts.BUSY_USER)
        wait = gate.limiter.hit(uid)
        if wait:
            log.warning("rate_limited", extra={"user_id": uid, "kind": "file", "retry_after": round(wait, 1)})
            return await message.answer(f"Fayl yuborish limiti tugadi. {int(wait) // 60 + 1} daqiqadan keyin urinib ko'ring.")

        gate.active.add(uid)
        status_msg = None
        try:
            status_msg = await message.answer(texts.WORKING)
            buf = io.BytesIO()
            await message.bot.download(doc, destination=buf)
            data = buf.getvalue()
            if len(data) > settings.max_file_bytes:
                log.warning("invalid_file", extra={"user_id": uid, "reason": "size_after_download", "size": len(data)})
                return await message.answer(texts.TOO_BIG)

            async with gate.global_sem:
                clean, fmt, report = await worker.sanitize(data, ext)

            await message.answer_document(
                BufferedInputFile(clean, filename=output_filename(doc.file_name, fmt)),
                caption=texts.caption(report),
            )
            await message.answer(texts.format_report(report))
            await asyncio.to_thread(
                db.record_job, uid, fmt, "ok", report.get("input_sha256"), report.get("output_sha256"),
                int(report.get("removed_count", 0)), doc.file_name, report,
            )
            log.info("job_ok", extra={"user_id": uid, "fmt": fmt, "size": len(data), "neutralized": report.get("removed_count", 0)})
        except WorkerRejected as exc:
            log.warning("job_rejected", extra={"user_id": uid, "ext": ext, "reason": str(exc)[:120]})
            await asyncio.to_thread(db.record_job, uid, ext, "rejected", None, None, 0, doc.file_name, None)
            await message.answer(f"Fayl qabul qilinmadi: {texts.safe_text(exc, 250)}")
        except WorkerBusy:
            log.warning("worker_busy", extra={"user_id": uid})
            await message.answer(texts.BUSY)
        except WorkerError as exc:
            log.error("worker_error", extra={"user_id": uid, "detail": str(exc)[:120]})
            await asyncio.to_thread(db.record_job, uid, ext, "error", None, None, 0, None, None)
            await message.answer(texts.GENERIC_ERROR)
        finally:
            gate.active.discard(uid)
            if status_msg is not None:
                try:
                    await status_msg.delete()
                except Exception:  # noqa: BLE001 - xabarni o'chirib bo'lmasa muhim emas
                    log.debug("status_message_delete_failed", exc_info=True)

    @r.message()
    async def fallback(message: Message) -> None:
        await message.answer(texts.NOT_A_FILE)

    return r
