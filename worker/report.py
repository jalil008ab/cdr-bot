"""Hisobot (report) modeli: nima topildi va nima qilindi.

Hujjat ichidan chiqqan matnlar (fayl nomlari, URL, metadata) ISHONCHSIZ hisoblanadi,
shuning uchun hisobotga yozishdan oldin tozalanadi (boshqaruv/bidi belgilar olib tashlanadi,
URL'lar "defang" qilinadi, uzunligi cheklanadi).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field

SEVERITIES = ("high", "medium", "low", "info")
_MAX_DETAIL = 160
_MAX_FINDINGS = 500  # hujumchi minglab finding yaratib hisobotni shishirib yubormasin


def clean_text(value: object, limit: int = _MAX_DETAIL) -> str:
    """Ishonchsiz matnni xavfsiz ko'rinishga keltiradi."""
    text = str(value)
    # Cc (boshqaruv) va Cf (format: RLO/LRO bidi, zero-width) belgilarni tashlaymiz
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def defang(value: object, limit: int = _MAX_DETAIL) -> str:
    """URL/yo'llarni bosib bo'lmaydigan ko'rinishga keltiradi (hxxp://evil[.]com)."""
    text = clean_text(value, limit)
    text = re.sub(r"(?i)\bhttp", "hxxp", text)
    text = re.sub(r"(?i)\bftp", "fxp", text)
    text = re.sub(r"(?i)\bfile:", "fxle:", text)
    text = text.replace(".", "[.]")
    return text


@dataclass
class Finding:
    category: str  # masalan: macro, ole, external_link, javascript, metadata
    severity: str  # high | medium | low | info
    location: str  # fayl ichidagi joy (part nomi yoki PDF obyekti)
    detail: str
    action: str = "removed"  # removed | neutralized | kept | rejected

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"bad severity: {self.severity}")
        self.location = clean_text(self.location, 120)
        self.detail = clean_text(self.detail)


@dataclass
class Report:
    fmt: str = ""  # docx | xlsx | pdf
    input_size: int = 0
    output_size: int = 0
    input_sha256: str = ""
    output_sha256: str = ""
    converted_from: str = ""  # masalan: "docm -> docx"
    findings: list[Finding] = field(default_factory=list)
    truncated: int = 0
    verified: bool = False

    def add(self, category: str, severity: str, location: str, detail: str, action: str = "removed") -> None:
        if len(self.findings) >= _MAX_FINDINGS:
            self.truncated += 1
            return
        self.findings.append(Finding(category, severity, location, detail, action))

    @property
    def removed_count(self) -> int:
        """Zararsizlantirilgan (info bo'lmagan) elementlar soni."""
        return sum(1 for f in self.findings if f.severity != "info") + self.truncated

    def to_dict(self) -> dict:
        data = asdict(self)
        data["removed_count"] = self.removed_count
        return data


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
