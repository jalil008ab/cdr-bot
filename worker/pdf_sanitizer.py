"""PDF uchun CDR dvigateli (pikepdf/qpdf ustida, zararsizlantirish mantig'i o'zimizniki).

Qilinadigan ishlar:
  * /JavaScript, /JS, /OpenAction, /AA, /Launch, /SubmitForm, /ImportData, /GoToR, /Rich Media va h.k. harakatlar olib tashlanadi;
    faqat ichki GoTo va xavfsiz URI (http/https/mailto) harakatlari qoladi.
  * O'rnatilgan fayllar (EmbeddedFiles, FileAttachment, /AF), XFA, multimedia annotatsiyalari o'chiriladi.
  * Metadata (/Info, XMP, PieceInfo, Thumb) o'chiriladi.
  * Fayl qayta yoziladi: erishib bo'lmaydigan (orphan) obyektlar tushib qoladi.
  * Natija qayta ochilib, taqiqlangan kalitlar qolmaganligi va sahifalar soni saqlanganligi tekshiriladi.
Oqimlar (streams) hech qachon dekodlanmaydi, shuning uchun siqish-bomba bu yerda ishlamaydi.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import pikepdf
from pikepdf import Name

from .errors import Rejected, VerificationFailed
from .report import Report, clean_text, defang

# Har qanday lug'atdan so'zsiz olib tashlanadigan kalitlar: kalit -> (kategoriya, xavf, tavsif)
_DELETE_KEYS: dict[str, tuple[str, str, str]] = {
    "/AA": ("auto_action", "high", "Avtomatik harakatlar (/AA)"),
    "/OpenAction": ("auto_action", "high", "Ochilganda ishga tushuvchi harakat (/OpenAction)"),
    "/JS": ("javascript", "high", "JavaScript kodi (/JS)"),
    "/JavaScript": ("javascript", "high", "JavaScript daraxti (/JavaScript)"),
    "/XFA": ("xfa", "high", "XFA shakl (skript saqlashi mumkin)"),
    "/RichMediaContent": ("rich_media", "high", "RichMedia kontent"),
    "/RichMediaSettings": ("rich_media", "high", "RichMedia sozlamalari"),
    "/EmbeddedFiles": ("embedded_file", "high", "O'rnatilgan fayllar (/EmbeddedFiles)"),
    "/EF": ("embedded_file", "high", "O'rnatilgan fayl oqimi (/EF)"),
    "/AF": ("embedded_file", "high", "Biriktirilgan fayllar (/AF)"),
    "/Launch": ("launch", "high", "Launch harakati"),
    "/Perms": ("permissions", "low", "Foydalanish huquqlari imzosi (/Perms)"),
    "/Collection": ("portfolio", "medium", "PDF portfolio (/Collection)"),
    "/Metadata": ("metadata", "low", "XMP metadata"),
    "/PieceInfo": ("metadata", "low", "Ilova ma'lumoti (/PieceInfo)"),
    "/Thumb": ("metadata", "low", "Sahifa eskizi (/Thumb)"),
}
_SAFE_ACTIONS = {"/GoTo", "/URI"}
_SAFE_URI_SCHEMES = ("http://", "https://", "mailto:")
_BAD_ANNOTS = {"/Screen", "/Movie", "/Sound", "/RichMedia", "/3D", "/FileAttachment"}
_INFO_FIELDS = ("/Author", "/Creator", "/Producer", "/Title", "/Subject")

MAX_PAGES = 1000
MAX_OBJECTS = 1_000_000
MAX_WALK = 3_000_000


@dataclass(frozen=True)
class PdfLimits:
    max_pages: int = MAX_PAGES
    max_objects: int = MAX_OBJECTS


def _is_dict(obj) -> bool:
    return isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream))


def _action_type(obj) -> str | None:
    try:
        s = obj.get("/S")
    except Exception:  # noqa: BLE001
        return None
    return str(s) if s is not None else None


def _looks_like_action(obj) -> bool:
    if not _is_dict(obj):
        return False
    return "/S" in obj or str(obj.get("/Type", "")) == "/Action"


def _walk(pdf: pikepdf.Pdf, report: Report, *, dry_verify: bool = False) -> int:
    """Barcha erishiladigan obyektlarni aylanib chiqadi. dry_verify=True bo'lsa hech narsani o'zgartirmaydi,
    faqat topilgan taqiqlangan elementlar sonini qaytaradi."""
    found = 0
    stack = [pdf.trailer]
    visited: set[tuple[int, int]] = set()
    steps = 0
    while stack:
        steps += 1
        if steps > MAX_WALK:
            raise Rejected("PDF tuzilmasi haddan tashqari murakkab.")
        obj = stack.pop()
        og = obj.objgen if getattr(obj, "is_indirect", False) else None
        if og is not None:
            if og in visited:
                continue
            visited.add(og)

        if isinstance(obj, pikepdf.Array):
            for item in obj:
                stack.append(item)
            continue
        if not _is_dict(obj):
            continue

        # /Type /EmbeddedFile oqimi erishiladigan bo'lsa — ham topilma
        if str(obj.get("/Type", "")) == "/EmbeddedFile":
            found += 1

        for key in list(obj.keys()):
            if key in _DELETE_KEYS:
                found += 1
                if not dry_verify:
                    cat, sev, label = _DELETE_KEYS[key]
                    report.add(cat, sev, "PDF", label, "removed")
                    del obj[key]
                continue
            if key == "/A":
                action = obj.get("/A")
                if _looks_like_action(action):
                    atype = _action_type(action)
                    ok = atype in _SAFE_ACTIONS
                    if ok and atype == "/URI":
                        uri = str(action.get("/URI", ""))
                        ok = uri.lower().startswith(_SAFE_URI_SCHEMES) and len(uri) <= 2048
                    if ok and "/Next" in action:  # zanjirli harakatlar yo'q
                        found += 1
                        if not dry_verify:
                            del action["/Next"]
                    if not ok:
                        found += 1
                        if not dry_verify:
                            sev = "high" if atype in ("/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR") else "medium"
                            detail = f"Harakat turi {clean_text(atype or '?', 30)}"
                            if atype == "/URI":
                                detail += f": {defang(action.get('/URI', ''), 80)}"
                            report.add("action", sev, "PDF", detail, "removed")
                            del obj["/A"]
                        continue
            if key in obj:
                stack.append(obj.get(key))
    return found


def _drop_bad_annotations(pdf: pikepdf.Pdf, report: Report) -> None:
    for pageno, page in enumerate(pdf.pages, start=1):
        annots = page.obj.get("/Annots")
        if annots is None or not isinstance(annots, pikepdf.Array):
            continue
        keep = pikepdf.Array()
        for annot in annots:
            subtype = str(annot.get("/Subtype", "")) if _is_dict(annot) else ""
            if subtype in _BAD_ANNOTS:
                report.add("annotation", "high", f"sahifa {pageno}", f"Xavfli annotatsiya turi {clean_text(subtype, 20)}", "removed")
            else:
                keep.append(annot)
        if len(keep) != len(annots):
            page.obj["/Annots"] = keep


def _strip_info(pdf: pikepdf.Pdf, report: Report) -> None:
    info = pdf.trailer.get("/Info")
    if info is None:
        return
    parts = []
    for field_name in _INFO_FIELDS:
        try:
            val = info.get(field_name)
        except Exception:  # noqa: BLE001
            val = None
        if val is not None and str(val).strip():
            parts.append(f"{field_name[1:].lower()}: {clean_text(val, 40)}")
    if parts:
        report.add("metadata", "low", "PDF /Info", "; ".join(parts[:4]), "removed")
    del pdf.trailer["/Info"]


def verify_pdf(clean: bytes, expected_pages: int) -> None:
    try:
        with pikepdf.open(io.BytesIO(clean)) as pdf:
            if len(pdf.pages) != expected_pages:
                raise VerificationFailed("Sahifalar soni o'zgarib ketdi.")
            leftovers = _walk(pdf, Report(), dry_verify=True)
            if leftovers:
                raise VerificationFailed(f"Qayta skanerlashda {leftovers} ta taqiqlangan element qoldi.")
            for page in pdf.pages:
                _ = page.mediabox  # sahifa asosiy o'lchamlari o'qilishi kerak
    except VerificationFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VerificationFailed(f"Tozalangan PDF qayta ochilmadi: {type(exc).__name__}") from exc


def sanitize_pdf(data: bytes, limits: PdfLimits | None = None) -> tuple[bytes, Report]:
    limits = limits or PdfLimits()
    if not data.startswith(b"%PDF-"):
        raise Rejected("Fayl PDF sarlavhasi bilan boshlanmaydi.")
    report = Report(fmt="pdf")
    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except pikepdf.PasswordError as exc:
        raise Rejected("Parol bilan himoyalangan PDF qo'llab-quvvatlanmaydi.") from exc
    except (pikepdf.PdfError, ValueError, RuntimeError) as exc:
        raise Rejected("PDF buzilgan yoki o'qib bo'lmadi.") from exc

    with pdf:
        page_count = len(pdf.pages)
        if page_count == 0 or page_count > limits.max_pages:
            raise Rejected(f"PDF sahifalari soni qabul qilinmaydi ({page_count}).")
        if len(pdf.objects) > limits.max_objects:
            raise Rejected("PDF obyektlari soni haddan tashqari ko'p.")
        if pdf.is_encrypted:
            report.add("encryption", "low", "PDF", "Foydalanish cheklovlari (owner parol) olib tashlandi", "removed")

        _drop_bad_annotations(pdf, report)
        _walk(pdf, report)
        _strip_info(pdf, report)

        out = io.BytesIO()
        pdf.save(
            out,
            linearize=False,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
            stream_decode_level=pikepdf.StreamDecodeLevel.none,
            encryption=False,
            deterministic_id=True,
            preserve_pdfa=False,
        )
    clean = out.getvalue()
    verify_pdf(clean, page_count)
    report.verified = True
    return clean, report
