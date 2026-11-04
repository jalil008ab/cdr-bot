"""Worker (alohida konteyner) bilan aloqa. Worker javobiga ham to'liq ishonilmaydi: tuzilma va hajm tekshiriladi."""
from __future__ import annotations

import base64
import binascii

import aiohttp


class WorkerRejected(Exception):
    """Fayl rad etildi; xabar foydalanuvchiga ko'rsatish uchun xavfsiz (worker o'zi yozgan)."""


class WorkerBusy(Exception):
    pass


class WorkerError(Exception):
    pass


class WorkerClient:
    def __init__(self, base_url: str, token: str, max_out_bytes: int, timeout: float = 70.0):
        self._url = base_url + "/v1/sanitize"
        self._token = token
        self._max_out = max_out_bytes
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def sanitize(self, data: bytes, ext: str) -> tuple[bytes, str, dict]:
        headers = {"X-Worker-Token": self._token, "X-Declared-Ext": ext, "Content-Type": "application/octet-stream"}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(self._url, data=data, headers=headers) as resp:
                    payload = await resp.json(content_type=None)
                    status = resp.status
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise WorkerError(f"worker_unreachable:{type(exc).__name__}") from exc

        if status == 503:
            raise WorkerBusy()
        if status == 422:
            raise WorkerRejected(str(payload.get("message", "Fayl rad etildi."))[:300])
        if status != 200 or not isinstance(payload, dict) or payload.get("status") != "ok":
            raise WorkerError(f"worker_status:{status}:{str(payload.get('status') if isinstance(payload, dict) else '?')[:30]}")

        fmt = payload.get("format")
        report = payload.get("report")
        b64 = payload.get("file_b64")
        if fmt not in ("pdf", "docx", "xlsx") or not isinstance(report, dict) or not isinstance(b64, str):
            raise WorkerError("worker_bad_payload")
        if len(b64) > self._max_out * 4 // 3 + 16:
            raise WorkerError("worker_output_too_large")
        try:
            clean = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise WorkerError("worker_bad_base64") from exc
        if len(clean) > self._max_out:
            raise WorkerError("worker_output_too_large")
        return clean, fmt, report
