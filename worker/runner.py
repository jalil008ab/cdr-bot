"""Job jarayonini ishga tushirish: argument ro'yxati (shell yo'q), alohida vaqtinchalik papka,
devor-soat (wall-clock) timeout, jarayon guruhini o'ldirish, har doim tozalash."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunnerConfig:
    wall_timeout: float = 40.0
    cpu_seconds: int = 20
    mem_mb: int = 900
    max_in_bytes: int = 25 * 1024 * 1024
    max_out_bytes: int = 120 * 1024 * 1024
    tmp_dir: str | None = None
    app_root: str = field(default_factory=lambda: str(Path(__file__).resolve().parent.parent))


@dataclass
class JobResult:
    status: str  # ok | rejected | timeout | resource | error
    message: str = ""
    fmt: str = ""
    report: dict | None = None
    data: bytes = b""


def _env(cfg: RunnerConfig, workdir: str) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": cfg.app_root,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": workdir,
        "TMPDIR": workdir,
        "LANG": "C.UTF-8",
        "JOB_CPU_SECONDS": str(cfg.cpu_seconds),
        "JOB_MEM_MB": str(cfg.mem_mb),
    }


async def run_job(data: bytes, declared_ext: str, cfg: RunnerConfig) -> JobResult:
    if len(data) > cfg.max_in_bytes:
        return JobResult("rejected", "Fayl hajmi limitdan katta.")
    if not declared_ext.isalnum() or len(declared_ext) > 8:
        return JobResult("rejected", "Kengaytma yaroqsiz.")

    workdir = tempfile.mkdtemp(prefix="job-", dir=cfg.tmp_dir)
    os.chmod(workdir, 0o700)
    try:
        in_path = os.path.join(workdir, "in.bin")
        out_path = os.path.join(workdir, "out.bin")
        res_path = os.path.join(workdir, "result.json")
        fd = os.open(in_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "worker.job", in_path, out_path, res_path, declared_ext,
            cwd=workdir,
            env=_env(cfg, workdir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=cfg.wall_timeout)
        except asyncio.TimeoutError:
            _kill_group(proc.pid)
            await proc.wait()
            return JobResult("timeout", "Qayta ishlash vaqt limitidan oshdi.")
        finally:
            if proc.returncode is None:
                _kill_group(proc.pid)

        if proc.returncode != 0:
            # SIGKILL/SIGXCPU/SIGSEGV va h.k.: limit buzildi yoki parser qulab tushdi
            return JobResult("resource", "Qayta ishlash resurs limitiga urildi yoki to'xtadi.")

        if not os.path.exists(res_path) or os.path.getsize(res_path) > 2 * 1024 * 1024:
            return JobResult("error", "no_result")
        with open(res_path, "r", encoding="utf-8") as fh:
            result = json.load(fh)
        status = result.get("status", "error")
        if status != "ok":
            return JobResult(status, str(result.get("message", "")))
        if not os.path.exists(out_path) or os.path.getsize(out_path) > cfg.max_out_bytes:
            return JobResult("error", "bad_output")
        with open(out_path, "rb") as fh:
            clean = fh.read()
        return JobResult("ok", fmt=result["format"], report=result["report"], data=clean)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)  # vaqtinchalik fayllar har doim o'chiriladi


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
