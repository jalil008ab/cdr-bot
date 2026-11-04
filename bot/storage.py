"""SQLite saqlash: faqat parametrlangan SQL, maxfiy maydonlar Fernet bilan shifrlangan,
audit log hash-zanjir (tamper-evident) ko'rinishida. Fayl MAZMUNI hech qachon saqlanmaydi."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time

from cryptography.fernet import Fernet

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    banned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    fmt TEXT NOT NULL,
    status TEXT NOT NULL,
    in_sha256 TEXT,
    out_sha256 TEXT,
    findings INTEGER NOT NULL DEFAULT 0,
    filename_enc BLOB,
    report_enc BLOB
);
CREATE INDEX IF NOT EXISTS idx_jobs_ts ON jobs(ts);
CREATE TABLE IF NOT EXISTS audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    actor_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    details TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""


def _audit_hash(prev: str, ts: int, actor: int, action: str, target: str, details: str) -> str:
    raw = "\x1f".join([prev, str(ts), str(actor), action, target, details])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: str, key: bytes):
        self._fernet = Fernet(key)
        self._lock = threading.RLock()
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    # ---- shifrlash
    def _enc(self, text: str) -> bytes:
        return self._fernet.encrypt(text.encode("utf-8"))

    def _dec(self, blob: bytes | None) -> str | None:
        return None if blob is None else self._fernet.decrypt(blob).decode("utf-8")

    # ---- foydalanuvchilar
    def touch_user(self, user_id: int) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO users(user_id, first_seen, last_seen) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen",
                (user_id, now, now),
            )

    def is_banned(self, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT banned FROM users WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row["banned"])

    def set_banned(self, user_id: int, banned: bool) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO users(user_id, first_seen, last_seen, banned) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET banned=excluded.banned",
                (user_id, now, now, 1 if banned else 0),
            )

    # ---- ishlar (jobs)
    def record_job(
        self, user_id: int, fmt: str, status: str, in_sha: str | None, out_sha: str | None,
        findings: int, filename: str | None, report: dict | None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO jobs(user_id, ts, fmt, status, in_sha256, out_sha256, findings, filename_enc, report_enc) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    user_id, int(time.time()), fmt, status, in_sha, out_sha, findings,
                    self._enc(filename) if filename else None,
                    self._enc(json.dumps(report, ensure_ascii=False)) if report else None,
                ),
            )
            return int(cur.lastrowid)

    def get_job_filename(self, job_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT filename_enc FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._dec(row["filename_enc"]) if row else None

    def stats(self) -> dict:
        since = int(time.time()) - 86400
        with self._lock:
            users = self._conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            banned = self._conn.execute("SELECT COUNT(*) c FROM users WHERE banned=1").fetchone()["c"]
            total = self._conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
            ok24 = self._conn.execute("SELECT COUNT(*) c FROM jobs WHERE ts>=? AND status='ok'", (since,)).fetchone()["c"]
            bad24 = self._conn.execute("SELECT COUNT(*) c FROM jobs WHERE ts>=? AND status<>'ok'", (since,)).fetchone()["c"]
            found = self._conn.execute("SELECT COALESCE(SUM(findings),0) s FROM jobs WHERE status='ok'").fetchone()["s"]
        return {"users": users, "banned": banned, "jobs": total, "ok_24h": ok24, "failed_24h": bad24, "neutralized_total": found}

    def purge_old(self, days: int) -> int:
        """Ma'lumotni minimallashtirish: eski ishlar o'chiriladi (audit log saqlanadi)."""
        cutoff = int(time.time()) - days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM jobs WHERE ts<?", (cutoff,))
            return cur.rowcount

    # ---- audit log (hash-zanjir)
    def audit(self, actor_id: int, action: str, target: str = "", details: str = "") -> int:
        ts = int(time.time())
        with self._lock:
            row = self._conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else GENESIS
            h = _audit_hash(prev, ts, actor_id, action, target, details)
            cur = self._conn.execute(
                "INSERT INTO audit(ts, actor_id, action, target, details, prev_hash, hash) VALUES(?,?,?,?,?,?,?)",
                (ts, actor_id, action, target, details, prev, h),
            )
            return int(cur.lastrowid)

    def recent_audit(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, actor_id, action, target, details FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def verify_audit_chain(self) -> tuple[bool, int | None]:
        """(ok, birinchi_buzilgan_id)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, actor_id, action, target, details, prev_hash, hash FROM audit ORDER BY id ASC"
            ).fetchall()
        prev = GENESIS
        for r in rows:
            expect = _audit_hash(prev, r["ts"], r["actor_id"], r["action"], r["target"], r["details"])
            if r["prev_hash"] != prev or r["hash"] != expect:
                return False, int(r["id"])
            prev = r["hash"]
        return True, None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
