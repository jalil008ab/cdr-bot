"""Format aniqlash va tegishli tozalagichni chaqirish. Job subprocess ichida ishlaydi."""
from __future__ import annotations

from .errors import Rejected
from .ooxml import sanitize_ooxml
from .pdf_sanitizer import sanitize_pdf
from .report import Report, sha256_hex
from .zipsafe import ZipLimits

OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_EXT_TO_FMT = {"pdf": "pdf", "docx": "docx", "docm": "docx", "xlsx": "xlsx", "xlsm": "xlsx"}


def sniff(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(OLE_MAGIC):
        return "ole"
    return "unknown"


def process(data: bytes, declared_ext: str, limits: ZipLimits | None = None) -> tuple[bytes, Report]:
    declared_ext = declared_ext.lower().lstrip(".")
    expected = _EXT_TO_FMT.get(declared_ext)
    if expected is None:
        raise Rejected("Qo'llab-quvvatlanadigan formatlar: PDF, DOCX, XLSX (DOCM/XLSM ham qabul qilinadi).")

    kind = sniff(data)
    if kind == "ole":
        raise Rejected("Eski binar format (.doc/.xls) qo'llab-quvvatlanmaydi. Uni .docx/.xlsx ko'rinishida saqlab qayta yuboring.")
    if kind == "unknown":
        raise Rejected("Fayl mazmuni PDF/DOCX/XLSX ga o'xshamaydi.")

    if kind == "pdf":
        if expected != "pdf":
            raise Rejected("Fayl kengaytmasi va haqiqiy mazmuni mos emas.")
        clean, report = sanitize_pdf(data)
    else:
        if expected == "pdf":
            raise Rejected("Fayl kengaytmasi va haqiqiy mazmuni mos emas.")
        clean, fmt, report = sanitize_ooxml(data, limits)
        if fmt != expected:
            raise Rejected("Fayl kengaytmasi va haqiqiy mazmuni mos emas.")

    report.input_size = len(data)
    report.output_size = len(clean)
    report.input_sha256 = sha256_hex(data)
    report.output_sha256 = sha256_hex(clean)
    return clean, report
