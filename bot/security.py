"""Xavfsizlik yordamchilari: rate limiter, fayl nomini tozalash, ruxsat etilgan kengaytmalar."""
from __future__ import annotations

import re
import time
import unicodedata
from collections import deque
from typing import Callable

ALLOWED_EXTS = {"pdf", "docx", "docm", "xlsx", "xlsm"}
_MAX_NAME = 80


class SlidingWindowLimiter:
    """Foydalanuvchi bo'yicha sirpanuvchi oyna. hit() 0 qaytarsa ruxsat, aks holda necha soniya kutish kerakligi."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic, max_keys: int = 50_000):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self._events: dict[int, deque[float]] = {}

    def hit(self, key: int) -> float:
        now = self.clock()
        if len(self._events) > self.max_keys:
            self._purge(now)
        q = self._events.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(self.window - (now - q[0]), 0.1)
        q.append(now)
        return 0.0

    def _purge(self, now: float) -> None:
        for k in [k for k, q in self._events.items() if not q or now - q[-1] >= self.window]:
            del self._events[k]


def split_ext(filename: str | None) -> tuple[str, str]:
    """(xavfsiz_nom_kengaytmasiz, kengaytma_kichik_harf). Kengaytma yo'q bo'lsa ''."""
    name = unicodedata.normalize("NFKC", filename or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")  # boshqaruv/bidi belgilar
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^\w\- ]", "_", stem, flags=re.UNICODE).strip(" ._")
    stem = re.sub(r"\s+", " ", stem)[:_MAX_NAME] or "document"
    return stem, ext.lower()


def output_filename(original: str | None, fmt: str) -> str:
    stem, _ = split_ext(original)
    return f"{stem}_clean.{fmt}"


def is_allowed_ext(ext: str) -> bool:
    return ext in ALLOWED_EXTS
