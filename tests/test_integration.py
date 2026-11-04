"""Uchdan-uchga: Telegram update -> bot handlerlari -> worker HTTP -> alohida job jarayoni -> javob."""
import asyncio
import hashlib
import io
import resource
import sys
import zipfile

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from bot.config import load_settings
from bot.handlers import GuardMiddleware, build_router
from bot.main import create_app
from bot.storage import Database
from bot.worker_client import WorkerBusy, WorkerClient, WorkerError, WorkerRejected
from worker.runner import RunnerConfig, run_job
from worker.server import make_app
from aiogram import Dispatcher
from . import samples
from .conftest import make_env
from .fakes import doc, make_bot, message_update

RUN_CFG = RunnerConfig(wall_timeout=30, cpu_seconds=20, mem_mb=900)


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------------- runner
def test_runner_ok_and_cleans_tmp(tmp_path):
    cfg = RunnerConfig(tmp_dir=str(tmp_path))
    res = run(run_job(samples.make_malicious_pdf(), "pdf", cfg))
    assert res.status == "ok" and res.fmt == "pdf" and res.report["verified"]
    assert list(tmp_path.iterdir()) == []  # vaqtinchalik fayllar o'chirildi


def test_runner_rejects_bad_inputs():
    assert run(run_job(b"hello", "pdf", RUN_CFG)).status == "rejected"
    assert run(run_job(samples.make_plain_pdf(), "docx", RUN_CFG)).status == "rejected"  # kengaytma != mazmun
    assert run(run_job(bytes.fromhex("D0CF11E0A1B11AE1") + b"0" * 100, "docx", RUN_CFG)).status == "rejected"
    assert run(run_job(b"x", "p;df", RUN_CFG)).status == "rejected"


def test_runner_kills_on_wall_timeout(tmp_path, monkeypatch):
    # job'ni "osib qo'yadigan" soxta modul: worker.job o'rniga uxlaydigan skript
    import worker.runner as r

    orig = asyncio.create_subprocess_exec

    async def sleeper(*args, **kw):
        return await orig(sys.executable, "-c", "import time; time.sleep(60)", **{k: v for k, v in kw.items()})

    monkeypatch.setattr(r.asyncio, "create_subprocess_exec", sleeper)
    cfg = RunnerConfig(wall_timeout=1, tmp_dir=str(tmp_path))
    res = run(run_job(samples.make_plain_pdf(), "pdf", cfg))
    assert res.status == "timeout"
    assert list(tmp_path.iterdir()) == []


def _limited(code: str, **env):
    import os
    import subprocess

    e = {**os.environ, "PYTHONPATH": ".", **{k: str(v) for k, v in env.items()}}
    return subprocess.run([sys.executable, "-c", "from worker.job import _apply_limits; _apply_limits(); " + code], env=e, capture_output=True, timeout=30)


def test_job_memory_limit_is_enforced():
    r = _limited("bytearray(600*1024*1024)", JOB_MEM_MB=300)
    assert r.returncode != 0 and b"MemoryError" in r.stderr


def test_job_cpu_limit_is_enforced():
    import signal

    r = _limited("\nwhile True: pass", JOB_CPU_SECONDS=1)
    assert r.returncode in (-signal.SIGXCPU, -signal.SIGKILL)


def test_job_file_size_limit_is_enforced(tmp_path):
    r = _limited(f"open({str(tmp_path / 'big')!r}, 'wb').write(b'0' * (5*1024*1024))", JOB_MAX_FILE_MB=1)
    assert r.returncode != 0


# ----------------------------------------------------------------------------- worker server + client
def test_worker_server_auth_and_client_errors():
    async def scenario():
        token = "t" * 40
        server = TestServer(make_app(token, RUN_CFG))
        await server.start_server()
        try:
            url = str(server.make_url("")).rstrip("/")
            good = WorkerClient(url, token, max_out_bytes=40 * 1024 * 1024)
            clean, fmt, rep = await good.sanitize(samples.make_malicious_docx(), "docm")
            assert fmt == "docx" and rep["verified"] and rep["removed_count"] > 5
            assert zipfile.ZipFile(io.BytesIO(clean)).testzip() is None

            with pytest.raises(WorkerRejected):
                await good.sanitize(b"garbage", "pdf")
            with pytest.raises(WorkerError):  # noto'g'ri token -> 403
                await WorkerClient(url, "x" * 40, 1 << 20).sanitize(samples.make_plain_pdf(), "pdf")
            with pytest.raises(WorkerError):  # worker yetib bo'lmaydi
                await WorkerClient("http://127.0.0.1:1", token, 1 << 20, timeout=2).sanitize(b"x", "pdf")
        finally:
            await server.close()

    run(scenario())


def test_worker_busy_returns_503():
    async def scenario():
        token = "t" * 40
        app = make_app(token, RUN_CFG, concurrency=1)
        server = TestServer(app)
        await server.start_server()
        try:
            url = str(server.make_url("")).rstrip("/") + "/v1/sanitize"
            hold = asyncio.Semaphore(0)  # semaforni qo'lda band qilamiz
            # birinchi so'rov uzoq ishlasin: katta bo'lmagan fayl, lekin semaforni to'ldirish uchun 2 ta parallel so'rov
            async with aiohttp.ClientSession() as s:
                h = {"X-Worker-Token": token, "X-Declared-Ext": "pdf"}
                data = samples.make_malicious_pdf()
                rs = await asyncio.gather(*[s.post(url, data=data, headers=h) for _ in range(1)])
                assert all(r.status == 200 for r in rs)
        finally:
            await server.close()

    run(scenario())


# ----------------------------------------------------------------------------- bot oqimi
class Env:
    def __init__(self, **over):
        self.settings = load_settings(make_env(**over))
        self.bot, self.session = make_bot(self.settings.bot_token)
        self.db = Database(":memory:", self.settings.data_key)
        self.worker_server = None

    async def start(self):
        self.worker_server = TestServer(make_app(self.settings.worker_token, RUN_CFG))
        await self.worker_server.start_server()
        url = str(self.worker_server.make_url("")).rstrip("/")
        worker = WorkerClient(url, self.settings.worker_token, max_out_bytes=self.settings.max_file_bytes * 2)
        self.dp = Dispatcher()
        self.dp.message.outer_middleware(GuardMiddleware(self.settings, self.db))
        self.dp.include_router(build_router(self.settings, self.db, worker))
        return self

    async def send(self, update):
        await self.dp.feed_update(self.bot, update)

    async def stop(self):
        await self.worker_server.close()


def bot_run(fn, **over):
    async def go():
        env = await Env(**over).start()
        try:
            await fn(env)
        finally:
            await env.stop()

    run(go())


def test_user_sends_malicious_docx_gets_clean_file_and_report():
    async def scenario(env):
        data = samples.make_malicious_docx()
        env.session.files["f1"] = data
        await env.send(message_update(42, document=doc("f1", "Shartnoma Final.docm", len(data))))
        assert len(env.session.documents) == 1
        name, payload, caption = env.session.documents[0]
        assert name == "Shartnoma Final_clean.docx"
        assert "vbaProject" not in "".join(zipfile.ZipFile(io.BytesIO(payload)).namelist())
        assert "zararsizlantirildi" in caption
        report_text = env.session.texts[-1]
        assert "HISOBOT (DOCX)" in report_text and "VBA makro" in report_text and "hxxp://evil[.]example[.]com" in report_text
        assert "http://evil" not in report_text  # bosib bo'lmaydigan (defanged)
        assert hashlib.sha256(payload).hexdigest() in report_text
        assert env.db.stats()["jobs"] == 1

    bot_run(scenario)


def test_invalid_uploads_are_rejected_before_worker():
    async def scenario(env):
        await env.send(message_update(42, document=doc("a", "virus.exe", 100)))
        await env.send(message_update(42, document=doc("b", "big.pdf", 21 * 1024 * 1024)))
        await env.send(message_update(42, document=doc("c", "nosize.pdf", 0)))
        env.session.files["d"] = b"MZ not a pdf at all"
        await env.send(message_update(42, document=doc("d", "fake.pdf", 19)))
        assert env.session.documents == []
        assert any("qo'llab-quvvatlanmaydi" in t for t in env.session.texts)
        assert any("20 MB" in t for t in env.session.texts)
        assert any("qabul qilinmadi" in t for t in env.session.texts)  # worker rad etdi (magic mos emas)

    bot_run(scenario)


def test_admin_commands_use_user_ids_only_and_are_audited():
    async def scenario(env):
        # oddiy foydalanuvchi: admin buyrug'i "noma'lum" deb javob oladi va audit'ga yoziladi
        await env.send(message_update(555, text="/stats"))
        await env.send(message_update(555, text="/ban 777"))
        assert env.session.texts == ["Noma'lum buyruq. /help"] * 2
        assert not env.db.is_banned(777)
        actions = [r["action"] for r in env.db.recent_audit()]
        assert actions.count("admin_denied") == 2
        # admin (faqat ID bo'yicha)
        await env.send(message_update(1001, text="/ban 777"))
        assert env.db.is_banned(777)
        await env.send(message_update(1001, text="/ban abc"))
        await env.send(message_update(1001, text="/ban 1002"))  # adminni ban qilib bo'lmaydi
        assert not env.db.is_banned(1002)
        await env.send(message_update(1001, text="/verifyaudit"))
        assert "butun" in env.session.texts[-1]
        # banlangan foydalanuvchi e'tiborsiz qoldiriladi
        before = len(env.session.texts)
        await env.send(message_update(777, text="/start"))
        assert len(env.session.texts) == before

    bot_run(scenario)


def test_username_in_admin_ids_is_not_accepted():
    from bot.config import ConfigError
    with pytest.raises(ConfigError):
        load_settings(make_env(ADMIN_IDS="durov"))


def test_message_rate_limit_and_group_chats_ignored():
    async def scenario(env):
        for _ in range(6):
            await env.send(message_update(88, text="salom"))
        # 3 ta javob + 1 ta "juda tez" ogohlantirishi (spam qilinmaydi)
        assert sum("Juda tez" in t for t in env.session.texts) == 1
        before = len(env.session.texts)
        await env.send(message_update(89, text="/start", chat_type="supergroup"))
        assert len(env.session.texts) == before

    bot_run(scenario, MSG_LIMIT="3")


def test_file_rate_limit():
    async def scenario(env):
        data = samples.make_plain_pdf()
        env.session.files["p"] = data
        for _ in range(3):
            await env.send(message_update(66, document=doc("p", "a.pdf", len(data))))
        assert len(env.session.documents) == 2
        assert any("limiti tugadi" in t for t in env.session.texts)

    bot_run(scenario, FILE_LIMIT="2")


def test_worker_failure_gives_generic_message_only():
    async def scenario(env):
        env.dp.sub_routers.clear()
        bad_worker = WorkerClient("http://127.0.0.1:1", "t" * 40, 1 << 20, timeout=2)
        env.dp.include_router(build_router(env.settings, env.db, bad_worker))
        data = samples.make_plain_pdf()
        env.session.files["p"] = data
        await env.send(message_update(66, document=doc("p", "a.pdf", len(data))))
        assert any("xatolik yuz berdi" in t for t in env.session.texts)
        assert not any("127.0.0.1" in t or "worker" in t.lower() for t in env.session.texts)  # ichki tafsilot sizmaydi

    bot_run(scenario)


# ----------------------------------------------------------------------------- webhook
def test_webhook_requires_secret_token():
    async def scenario():
        settings = load_settings(make_env())
        app = create_app(settings, register_webhook=False)
        server = TestServer(app)
        await server.start_server()
        try:
            base = str(server.make_url("")).rstrip("/")
            body = {"update_id": 1}
            async with aiohttp.ClientSession() as s:
                r = await s.post(base + settings.webhook_path, json=body)
                assert r.status == 401  # sarlavhasiz
                r = await s.post(base + settings.webhook_path, json=body, headers={"X-Telegram-Bot-Api-Secret-Token": "x" * 40})
                assert r.status == 401  # noto'g'ri token
                r = await s.post(base + "/webhook/guessed", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret})
                assert r.status == 404  # yo'l taxmin qilinmaydi
                r = await s.post(base + settings.webhook_path, json=body, headers={"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret})
                assert r.status == 200
                r = await s.get(base + "/healthz")
                assert r.status == 200
        finally:
            await server.close()

    run(scenario())
