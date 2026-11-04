"""Worker'ning ichki HTTP xizmati. Faqat 'internal' Docker tarmog'ida, umumiy token bilan himoyalangan.
Server fayl mazmunini O'ZI parse qilmaydi: hammasi alohida job jarayonida bajariladi."""
from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import os

from aiohttp import web

from .runner import JobResult, RunnerConfig, run_job

log = logging.getLogger("worker")
MAX_BODY = int(os.environ.get("MAX_FILE_MB", "20")) * 1024 * 1024 + 1024


def make_app(token: str, cfg: RunnerConfig, concurrency: int = 2) -> web.Application:
    sem = asyncio.Semaphore(concurrency)

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def sanitize(request: web.Request) -> web.Response:
        given = request.headers.get("X-Worker-Token", "")
        if not hmac.compare_digest(given.encode(), token.encode()):
            log.warning("worker_auth_failed")
            return web.json_response({"status": "forbidden"}, status=403)
        ext = request.headers.get("X-Declared-Ext", "")
        try:
            await asyncio.wait_for(sem.acquire(), timeout=5)
        except asyncio.TimeoutError:
            return web.json_response({"status": "busy"}, status=503)
        try:
            body = await request.read()
            result: JobResult = await run_job(body, ext, cfg)
        finally:
            sem.release()
        log.info("job_finished", extra={"status": result.status, "size": len(body)})
        if result.status == "ok":
            return web.json_response(
                {
                    "status": "ok",
                    "format": result.fmt,
                    "report": result.report,
                    "file_b64": base64.b64encode(result.data).decode("ascii"),
                }
            )
        code = {"rejected": 422, "timeout": 504, "resource": 507}.get(result.status, 500)
        return web.json_response({"status": result.status, "message": result.message}, status=code)

    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_get("/healthz", health)
    app.router.add_post("/v1/sanitize", sanitize)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
    token = os.environ.get("WORKER_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("WORKER_TOKEN kamida 32 belgi bo'lishi kerak")
    cfg = RunnerConfig(
        wall_timeout=float(os.environ.get("JOB_WALL_SECONDS", "40")),
        cpu_seconds=int(os.environ.get("JOB_CPU_SECONDS", "20")),
        mem_mb=int(os.environ.get("JOB_MEM_MB", "900")),
        max_in_bytes=int(os.environ.get("MAX_FILE_MB", "20")) * 1024 * 1024,
        tmp_dir=os.environ.get("JOB_TMP_DIR") or None,  # None = tizim vaqtinchalik papkasi (compose: tmpfs)
    )
    web.run_app(
        make_app(token, cfg, int(os.environ.get("WORKER_CONCURRENCY", "2"))),
        # Faqat "internal" Docker tarmog'ida tinglaydi (tashqi internetga yo'li yo'q)
        host="0.0.0.0",  # nosec B104
        port=8081,
        access_log=None,
    )


if __name__ == "__main__":
    main()
