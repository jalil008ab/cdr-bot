"""Bitta faylni ALOHIDA jarayonda qayta ishlaydi: python -m worker.job <in> <out> <result.json> <ext>

Jarayon boshida resurs limitlari (CPU, xotira, fayl hajmi, ochiq fayllar) o'rnatiladi, shuning uchun
hostile fayl parser'ni osib qo'ysa yoki xotirani to'ldirsa, faqat shu jarayon o'ladi.
"""
from __future__ import annotations

import json
import os
import resource
import sys

from .errors import Rejected, VerificationFailed


def _apply_limits() -> None:
    cpu = int(os.environ.get("JOB_CPU_SECONDS", "20"))
    mem = int(os.environ.get("JOB_MEM_MB", "900")) * 1024 * 1024
    fsize = int(os.environ.get("JOB_MAX_FILE_MB", "200")) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)


def _write_result(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def main(argv: list[str]) -> int:
    in_path, out_path, result_path, ext = argv[1:5]
    _apply_limits()
    try:
        # Og'ir kutubxonalar limitlar o'rnatilgandan KEYIN import qilinadi, shuning uchun ular ham limit ichida
        from .engine import process
        from .zipsafe import ZipLimits

        with open(in_path, "rb") as fh:
            data = fh.read()
        clean, report = process(data, ext, ZipLimits())
        with open(out_path, "wb") as fh:
            fh.write(clean)
        _write_result(result_path, {"status": "ok", "format": report.fmt, "report": report.to_dict()})
        return 0
    except Rejected as exc:
        _write_result(result_path, {"status": "rejected", "message": str(exc)})
        return 0
    except VerificationFailed:
        _write_result(result_path, {"status": "error", "message": "verification_failed"})
        return 0
    except MemoryError:
        _write_result(result_path, {"status": "resource", "message": "memory"})
        return 0
    except Exception as exc:  # noqa: BLE001 - tafsilot foydalanuvchiga chiqmaydi
        _write_result(result_path, {"status": "error", "message": f"internal:{type(exc).__name__}"})
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
