"""Foydalanuvchiga ko'rinadigan matnlar (o'zbekcha) va hisobotni formatlash. Hammasi oddiy matn (parse_mode yo'q)."""
from __future__ import annotations

import unicodedata

START = (
    "Salom! Men hujjat tozalovchi (CDR) botman.\n\n"
    "PDF, DOCX yoki XLSX faylni FAYL (document) ko'rinishida yuboring. Men uni xavfsiz muhitda qayta qurib, "
    "ichidagi zararli elementlarni olib tashlayman va nima o'chirilganini aytib beraman.\n\n"
    "/policy - nimalar o'chiriladi\n/help - yordam"
)
HELP = (
    "Foydalanish:\n"
    "1) Faylni siqmasdan, 'Fayl' sifatida yuboring (rasm/galereya emas).\n"
    "2) Qabul qilinadi: .pdf, .docx, .docm, .xlsx, .xlsm (20 MB gacha).\n"
    "3) Javobda tozalangan fayl va hisobot keladi.\n\n"
    "Eski .doc/.xls, parol bilan himoyalangan fayllar qabul qilinmaydi."
)
POLICY = (
    "Nimalar zararsizlantiriladi:\n"
    "- VBA makrolar va Excel 4.0 (XLM) makrolar\n"
    "- OLE obyektlar, ActiveX, o'rnatilgan fayllar\n"
    "- Tashqi shablon (remote template) va tashqi fayl havolalari\n"
    "- Word DDE/INCLUDETEXT maydonlari, xavfli Excel formulalari\n"
    "- PDF JavaScript, OpenAction, Launch, SubmitForm, XFA, ilova fayllar\n"
    "- Metadata (muallif, kompaniya, eskiz va h.k.)\n\n"
    "Oddiy veb-havolalar (http/https/mailto) saqlanadi. Faylingiz serverda saqlanmaydi."
)
GENERIC_ERROR = "Kechirasiz, faylni qayta ishlashda xatolik yuz berdi. Keyinroq urinib ko'ring."
BUSY = "Hozir server band. Birozdan keyin qayta urinib ko'ring."
NOT_A_FILE = "Iltimos, PDF, DOCX yoki XLSX faylni 'Fayl' ko'rinishida yuboring. /help"
BAD_EXT = "Bu fayl turi qo'llab-quvvatlanmaydi. Faqat: PDF, DOCX, DOCM, XLSX, XLSM."
TOO_BIG = "Fayl hajmi 20 MB dan oshmasligi kerak."
BUSY_USER = "Avvalgi faylingiz hali ishlanmoqda. Tugashini kuting."
WORKING = "Fayl xavfsiz muhitda qayta ishlanmoqda..."
BANNED = "Sizga botdan foydalanish cheklangan."
UNKNOWN_CMD = "Noma'lum buyruq. /help"

_SEV_LABEL = {"high": "YUQORI", "medium": "O'RTA", "low": "PAST", "info": "MA'LUMOT"}
_SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def safe_text(value: object, limit: int = 140) -> str:
    """Worker'dan kelgan matnni ham qayta tozalaymiz (defense in depth)."""
    text = "".join(ch for ch in str(value) if unicodedata.category(ch)[0] != "C" or ch == " ")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def caption(report: dict) -> str:
    n = int(report.get("removed_count", 0))
    if n == 0:
        return "Fayl tozalandi: xavfli element topilmadi."
    return f"Fayl tozalandi: {n} ta element zararsizlantirildi."


def format_report(report: dict, max_lines: int = 40, max_chars: int = 3800) -> str:
    fmt = safe_text(report.get("fmt", "?"), 8).upper()
    lines = [
        f"HISOBOT ({fmt})",
        f"Hajmi: {human_size(int(report.get('input_size', 0)))} -> {human_size(int(report.get('output_size', 0)))}",
        f"SHA-256 (asl):  {safe_text(report.get('input_sha256', ''), 64)}",
        f"SHA-256 (toza): {safe_text(report.get('output_sha256', ''), 64)}",
        "Tekshiruv: " + ("o'tdi (qayta skan + ochilish sinovi)" if report.get("verified") else "o'tmadi"),
    ]
    if report.get("converted_from"):
        lines.append(f"Format: {safe_text(report['converted_from'])}")
    findings = [f for f in report.get("findings", []) if isinstance(f, dict)]
    findings.sort(key=lambda f: _SEV_ORDER.get(f.get("severity"), 9))
    lines.append("")
    if not findings:
        lines.append("Hech qanday xavfli element topilmadi.")
    else:
        lines.append(f"Topilganlar ({len(findings)}):")
        used = sum(len(x) + 1 for x in lines)
        shown = 0
        for f in findings[:max_lines]:
            sev = _SEV_LABEL.get(f.get("severity"), "?")
            row = f"- [{sev}] {safe_text(f.get('detail', ''))} @ {safe_text(f.get('location', ''), 60)} -> {safe_text(f.get('action', ''), 20)}"
            if used + len(row) + 1 > max_chars - 120:  # oxirgi "yana N ta" qatori uchun joy qoldiramiz
                break
            lines.append(row)
            used += len(row) + 1
            shown += 1
        if len(findings) > shown:
            lines.append(f"... va yana {len(findings) - shown} ta")
    if report.get("truncated"):
        lines.append(f"(+{int(report['truncated'])} ta qo'shimcha topilma hisobotga sig'madi)")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
