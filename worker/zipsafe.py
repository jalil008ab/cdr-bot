"""Ishonchsiz ZIP arxivni xavfsiz o'qish.

Himoya qiladi: zip bomba (ratio, umumiy hajm), "quine"/overlapping zip bomba, path traversal,
dublikat nomlar (zip smuggling), shifrlangan yozuvlar, ortiqcha yozuvlar soni.
Sarlavhadagi hajmga ISHONILMAYDI: haqiqiy o'qilgan baytlar sanaladi.
"""
from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass

from .errors import UnsafeArchive


@dataclass(frozen=True)
class ZipLimits:
    max_entries: int = 3000
    max_total_uncompressed: int = 150 * 1024 * 1024
    max_entry_uncompressed: int = 80 * 1024 * 1024
    max_ratio: int = 150  # faqat katta (> ratio_min_size) yozuvlar uchun
    ratio_min_size: int = 1024 * 1024
    max_name_len: int = 260


_ALLOWED_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)


def _check_name(name: str, limits: ZipLimits) -> None:
    if not name or len(name) > limits.max_name_len:
        raise UnsafeArchive("Arxiv ichidagi fayl nomi yaroqsiz.")
    if "\x00" in name or "\\" in name or ":" in name:
        raise UnsafeArchive("Arxiv ichida xavfli fayl nomi topildi.")
    if name.startswith("/"):
        raise UnsafeArchive("Arxiv ichida absolyut yo'l topildi.")
    norm = posixpath.normpath(name)
    if norm.startswith("..") or "/../" in f"/{norm}/" or norm != name.rstrip("/"):
        raise UnsafeArchive("Arxiv ichida path traversal urinishi topildi.")


def validate_archive(zf: zipfile.ZipFile, limits: ZipLimits) -> list[zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > limits.max_entries:
        raise UnsafeArchive("Arxivda yozuvlar soni haddan tashqari ko'p.")

    seen: set[str] = set()
    total = 0
    files: list[zipfile.ZipInfo] = []
    for info in infos:
        _check_name(info.filename, limits)
        key = info.filename.rstrip("/").lower()
        if key in seen:
            raise UnsafeArchive("Arxivda bir xil nomli yozuvlar bor (zip smuggling).")
        seen.add(key)
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise UnsafeArchive("Shifrlangan arxiv yozuvi qo'llab-quvvatlanmaydi.")
        if info.compress_type not in _ALLOWED_COMPRESSION:
            raise UnsafeArchive("Arxivda qo'llab-quvvatlanmaydigan siqish usuli.")
        if info.file_size > limits.max_entry_uncompressed:
            raise UnsafeArchive("Arxiv ichidagi fayl haddan tashqari katta (zip bomba).")
        if (
            info.file_size > limits.ratio_min_size
            and info.file_size > max(info.compress_size, 1) * limits.max_ratio
        ):
            raise UnsafeArchive("Siqish koeffitsienti shubhali darajada yuqori (zip bomba).")
        total += info.file_size
        if total > limits.max_total_uncompressed:
            raise UnsafeArchive("Arxivning ochilgan umumiy hajmi limitdan oshdi (zip bomba).")
        files.append(info)

    # Overlapping entries (David Fifield "quine" zip bomba): bir xil baytlar bir necha yozuvga xizmat qiladi.
    ordered = sorted(files, key=lambda i: i.header_offset)
    for cur, nxt in zip(ordered, ordered[1:]):
        if cur.header_offset + 30 + cur.compress_size > nxt.header_offset:
            raise UnsafeArchive("Arxiv yozuvlari bir-birining ustiga tushgan (overlapping zip bomba).")
    return files


def safe_read_all(data: bytes, limits: ZipLimits | None = None) -> dict[str, bytes]:
    """Arxivni to'liq tekshirib, {nom: bayt} ko'rinishida qaytaradi (tartib saqlanadi)."""
    limits = limits or ZipLimits()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, EOFError, ValueError, NotImplementedError) as exc:
        raise UnsafeArchive("ZIP tuzilmasi buzilgan yoki yaroqsiz.") from exc

    out: dict[str, bytes] = {}
    with zf:
        files = validate_archive(zf, limits)
        total = 0
        for info in files:
            budget = min(limits.max_entry_uncompressed, limits.max_total_uncompressed - total)
            try:
                with zf.open(info) as fh:
                    buf = fh.read(budget + 1)  # haqiqiy o'qish ham cheklangan
            except (zipfile.BadZipFile, EOFError, RuntimeError, OSError, ValueError, NotImplementedError) as exc:
                raise UnsafeArchive("Arxiv yozuvini o'qib bo'lmadi (buzilgan).") from exc
            if len(buf) > budget:
                raise UnsafeArchive("Arxiv ichidagi fayl haqiqatda limitdan katta (zip bomba).")
            if len(buf) != info.file_size:
                raise UnsafeArchive("Arxiv sarlavhasidagi hajm haqiqiyga mos emas.")
            total += len(buf)
            out[info.filename] = buf
    return out
