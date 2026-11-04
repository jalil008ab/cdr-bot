"""DOCX/XLSX uchun CDR (Content Disarm & Reconstruction) dvigateli.

Yondashuv: eski faylni "tozalab" emas, YANGIDAN QURIB chiqamiz.
  1. Paketni xavfsiz o'qiymiz (zipsafe).
  2. Ildiz (_rels/.rels) dan boshlab munosabatlar (relationships) grafini aylanib chiqamiz.
     Faqat XAVFSIZ deb hisoblangan va grafdan erishiladigan qismlar (parts) o'tadi (allowlist).
  3. XML qismlardan o'chirilgan munosabatlarga havolalarni, Word maydon (DDE) va Excel xavfli
     formulalarini olib tashlaymiz.
  4. [Content_Types].xml va .rels fayllarini noldan quramiz, ZIP'ni qayta yozamiz.
  5. Natijani tekshiramiz (tuzilma, XML, havolalar, qayta-skan va haqiqiy kutubxonada ochish).
"""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from lxml import etree

from .errors import Rejected, VerificationFailed
from .report import Report, clean_text, defang
from .zipsafe import ZipLimits, safe_read_all

NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
R_NAMESPACES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "http://purl.oclc.org/ooxml/officeDocument/relationships",
}
SS_NS = (
    "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "http://purl.oclc.org/ooxml/spreadsheetml/main",
)
CT_RELS = "application/vnd.openxmlformats-package.relationships+xml"
CT_CT = "[Content_Types].xml"

_WORD_DOC = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_XL_SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
# main CT (kichik harf) -> (format, almashtiriladigan oddiy CT yoki None)
_MAIN_CT: dict[str, tuple[str, str | None]] = {
    _WORD_DOC.lower(): ("docx", None),
    "application/vnd.ms-word.document.macroenabled.main+xml": ("docx", _WORD_DOC),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml": ("docx", _WORD_DOC),
    "application/vnd.ms-word.template.macroenabledtemplate.main+xml": ("docx", _WORD_DOC),
    _XL_SHEET.lower(): ("xlsx", None),
    "application/vnd.ms-excel.sheet.macroenabled.main+xml": ("xlsx", _XL_SHEET),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml": ("xlsx", _XL_SHEET),
    "application/vnd.ms-excel.template.macroenabled.main+xml": ("xlsx", _XL_SHEET),
}

# Munosabat turi (oxirgi segment, kichik harf) -> (kategoriya, xavf darajasi, tavsif)
_REL_RULES: dict[str, tuple[str, str, str]] = {
    "vbaproject": ("macro", "high", "VBA makro loyihasi"),
    "vbaprojectsignature": ("macro", "high", "VBA makro imzosi"),
    "vbadata": ("macro", "high", "VBA ma'lumotlari"),
    "xlmacrosheet": ("macro", "high", "Excel 4.0 (XLM) makro varag'i"),
    "xlintlmacrosheet": ("macro", "high", "Excel 4.0 (XLM) makro varag'i"),
    "oleobject": ("ole", "high", "OLE obyekt"),
    "package": ("embedded", "high", "O'rnatilgan paket/fayl"),
    "activexcontrolbinary": ("activex", "high", "ActiveX boshqaruv elementi"),
    "control": ("activex", "high", "ActiveX boshqaruv elementi"),
    "ctrlprop": ("activex", "high", "ActiveX xossalari"),
    "attachedtemplate": ("remote_template", "high", "Biriktirilgan shablon (remote template)"),
    "externallink": ("external_link", "high", "Tashqi fayl havolasi"),
    "externallinkpath": ("external_link", "high", "Tashqi fayl havolasi"),
    "afchunk": ("altchunk", "high", "altChunk (o'rnatilgan boshqa hujjat)"),
    "subdocument": ("external_link", "high", "Sub-document havolasi"),
    "frame": ("external_link", "high", "Tashqi frame"),
    "connections": ("data_connection", "high", "Tashqi ma'lumot ulanishi"),
    "querytable": ("data_connection", "high", "Tashqi so'rov jadvali"),
    "extensibility": ("custom_ui", "medium", "Maxsus lenta (customUI, callback)"),
    "webextension": ("addin", "medium", "Office add-in"),
    "webextensiontaskpanes": ("addin", "medium", "Office add-in paneli"),
    "printersettings": ("printer", "low", "Printer sozlamalari (binar)"),
    "core-properties": ("metadata", "low", "Hujjat metadatasi (core)"),
    "extended-properties": ("metadata", "low", "Hujjat metadatasi (app)"),
    "custom-properties": ("metadata", "low", "Maxsus metadata"),
    "thumbnail": ("metadata", "low", "Eskiz (thumbnail)"),
    "customxml": ("metadata", "low", "Custom XML ma'lumoti"),
    "origin": ("signature", "low", "Raqamli imzo (o'zgargach yaroqsiz bo'ladi)"),
    "signature": ("signature", "low", "Raqamli imzo (o'zgargach yaroqsiz bo'ladi)"),
    "calcchain": ("calcchain", "info", "calcChain (Excel o'zi qayta quradi)"),
}

_DANGEROUS_PART_RE = re.compile(
    r"(^|/)(vbaproject|activex|embeddings|macrosheets|externallinks|customui)"
    r"|\.(bin|exe|dll|js|vbs|ps1|bat|cmd|scr|lnk|hta|jar|com|msi|sh|dat)$",
    re.I,
)
# Kontent turi bo'yicha rad etish (kichik harfdagi qism satr)
_CT_DENY: tuple[tuple[str, str, str, str], ...] = (
    ("vbaproject", "macro", "high", "VBA makro"),
    ("macrosheet", "macro", "high", "Excel 4.0 makro varag'i"),
    ("macroenabled", "macro", "high", "Makro yoqilgan paket"),
    ("activex", "activex", "high", "ActiveX"),
    ("oleobject", "ole", "high", "OLE obyekt"),
    ("externallink", "external_link", "high", "Tashqi havola"),
    ("connections", "data_connection", "high", "Ma'lumot ulanishi"),
    ("querytable", "data_connection", "high", "So'rov jadvali"),
    ("webextension", "addin", "medium", "Office add-in"),
    ("printersettings", "printer", "low", "Printer sozlamalari"),
    ("ctrlprop", "activex", "high", "ActiveX xossalari"),
    ("image/svg", "svg", "medium", "SVG (skript saqlashi mumkin)"),
    ("encrypted", "encrypted", "medium", "Shifrlangan qism"),
)
_OPAQUE_OK = {
    "application/vnd.openxmlformats-officedocument.vmldrawing",
    "application/vnd.openxmlformats-officedocument.obfuscatedfont",
}
_IMAGE_MAGIC = {
    "image/png": lambda d: d.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda d: d.startswith(b"\xff\xd8\xff"),
    "image/gif": lambda d: d.startswith((b"GIF87a", b"GIF89a")),
    "image/bmp": lambda d: d.startswith(b"BM"),
    "image/tiff": lambda d: d.startswith((b"II*\x00", b"MM\x00*")),
    "image/x-emf": lambda d: d[40:44] == b" EMF",
    "image/x-wmf": lambda d: d.startswith((b"\xd7\xcd\xc6\x9a", b"\x01\x00\x09\x00", b"\x02\x00\x09\x00")),
}
_SAFE_URL_SCHEMES = {"http", "https", "mailto", "tel"}

_WORD_DANGEROUS_FIELD = re.compile(
    r"^\s*(DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE|LINK|IMPORT|MACROBUTTON|DATABASE|AUTOOPEN|RUN)\b", re.I
)
_XL_EXTREF = re.compile(r"\[\d+\]")
_XL_DANGEROUS_FN = re.compile(
    r"\b(EXEC|CALL|REGISTER|REGISTER\.ID|FOPEN|FWRITE|FREAD|FILES|URLDOWNLOADTOFILE|SHELL)\s*\(", re.I
)
_XL_DDE = re.compile(r"^\s*[\w.\\:\-]+\s*\|")
_XL_AUTO_NAMES = re.compile(r"(^|\.)(auto_open|auto_close|auto_activate|auto_deactivate)$", re.I)


# --------------------------------------------------------------------------- XML yordamchilari
def _local(el: etree._Element) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def parse_xml(data: bytes, name: str) -> etree._Element:
    """Hostile XML'ni xavfsiz parse qilish: entity yo'q, tarmoq yo'q, DOCTYPE rad etiladi (XXE/billion laughs)."""
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
    )
    try:
        tree = etree.parse(io.BytesIO(data), parser)
    except (etree.LxmlError, ValueError) as exc:
        raise Rejected(f"XML buzilgan yoki xavfli: {clean_text(name, 60)}") from exc
    if tree.docinfo.doctype or tree.docinfo.internalDTD is not None:
        raise Rejected(f"XML'da DOCTYPE/entity e'loni topildi (XXE urinishi): {clean_text(name, 60)}")
    return tree.getroot()


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _remove(el: etree._Element) -> None:
    parent = el.getparent()
    if parent is None:
        return
    if el.tail:  # lxml remove() tail'ni ham olib ketadi, matnni saqlaymiz
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _unwrap(el: etree._Element) -> None:
    parent = el.getparent()
    if parent is None:
        return
    idx = parent.index(el)
    for offset, child in enumerate(list(el)):
        parent.insert(idx + offset, child)
    _remove(el)


# --------------------------------------------------------------------------- paket tuzilmasi
@dataclass
class _Rel:
    id: str
    type: str
    target: str
    external: bool


class _ContentTypes:
    def __init__(self, defaults: dict[str, str], overrides: dict[str, str]):
        self.defaults = defaults
        self.overrides = overrides

    def content_type(self, name: str) -> str | None:
        explicit = self.overrides.get("/" + name.lower())
        if explicit is not None:
            return explicit
        base = posixpath.basename(name)
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
        return self.defaults.get(ext)


def _parse_content_types(files: dict[str, bytes]) -> _ContentTypes:
    raw = files.get(CT_CT)
    if raw is None:
        raise Rejected("Bu OOXML (docx/xlsx) fayl emas: [Content_Types].xml topilmadi.")
    root = parse_xml(raw, CT_CT)
    if root.tag != f"{{{NS_CT}}}Types":
        raise Rejected("[Content_Types].xml tuzilmasi yaroqsiz.")
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for el in root:
        if not isinstance(el.tag, str):
            continue
        ct = el.get("ContentType")
        if not ct:
            continue
        if el.tag == f"{{{NS_CT}}}Default" and el.get("Extension"):
            defaults[el.get("Extension").lower()] = ct
        elif el.tag == f"{{{NS_CT}}}Override" and el.get("PartName"):
            overrides[el.get("PartName").lower()] = ct
    return _ContentTypes(defaults, overrides)


def _parse_rels(raw: bytes, name: str) -> list[_Rel]:
    root = parse_xml(raw, name)
    if root.tag != f"{{{NS_PKG_REL}}}Relationships":
        raise Rejected(f"Munosabatlar fayli yaroqsiz: {clean_text(name, 60)}")
    rels: list[_Rel] = []
    seen: set[str] = set()
    for el in root:
        if el.tag != f"{{{NS_PKG_REL}}}Relationship":
            continue
        rid, rtype, target = el.get("Id"), el.get("Type"), el.get("Target")
        if not rid or not rtype or target is None:
            raise Rejected(f"Munosabat yozuvi to'liq emas: {clean_text(name, 60)}")
        if rid in seen:
            raise Rejected(f"Munosabat Id'lari takrorlangan: {clean_text(name, 60)}")
        seen.add(rid)
        rels.append(_Rel(rid, rtype, target, (el.get("TargetMode") or "").lower() == "external"))
    return rels


def _rels_name(part: str) -> str:
    if part == "":
        return "_rels/.rels"
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def _resolve(source: str, target: str) -> str | None:
    t = unquote(target.split("#", 1)[0])
    if not t:
        return None
    name = t[1:] if t.startswith("/") else posixpath.join(posixpath.dirname(source), t)
    name = posixpath.normpath(name)
    if name in (".", "") or name.startswith("..") or name.startswith("/"):
        return None
    return name


def _is_safe_url(target: str) -> bool:
    if len(target) > 2048 or any(ord(c) < 32 for c in target):
        return False
    try:
        return urlsplit(target).scheme.lower() in _SAFE_URL_SCHEMES
    except ValueError:
        return False


def _is_xml_ct(ct: str | None) -> bool:
    c = (ct or "").lower()
    return c.endswith("+xml") or c in ("application/xml", "text/xml")


def _classify_part(name: str, ct: str | None, data: bytes) -> tuple[str, str, str] | None:
    """None = qism xavfsiz. Aks holda (kategoriya, xavf, tavsif) = o'chiriladi."""
    if _DANGEROUS_PART_RE.search(name.lower()):
        return ("suspicious_part", "high", "Xavfli nomli yoki turdagi fayl")
    if ct is None:
        return ("unknown_part", "medium", "Kontent turi noma'lum fayl")
    cl = ct.lower()
    for needle, cat, sev, label in _CT_DENY:
        if needle in cl:
            return (cat, sev, label)
    if _is_xml_ct(cl) or cl in _OPAQUE_OK:
        return None
    check = _IMAGE_MAGIC.get(cl)
    if check is not None:
        return None if check(data) else ("image_spoof", "medium", "Rasm sarlavhasi e'lon qilingan turga mos emas")
    return ("unsupported_part", "medium", f"Qo'llab-quvvatlanmaydigan qism turi ({clean_text(ct, 50)})")


@dataclass
class _Ctx:
    report: Report
    kind: str
    macro_sheets_removed: bool = False
    kept_links: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- 1-bosqich: reja
def _find_main(files: dict[str, bytes], idx: dict[str, str]) -> str:
    root_rels = idx.get("_rels/.rels")
    if root_rels is None:
        raise Rejected("Bu OOXML fayl emas: _rels/.rels topilmadi.")
    for rel in _parse_rels(files[root_rels], root_rels):
        if rel.type.rsplit("/", 1)[-1].lower() == "officedocument" and not rel.external:
            target = _resolve("", rel.target)
            if target and target.lower() in idx:
                return idx[target.lower()]
    raise Rejected("Hujjatning asosiy qismi topilmadi.")


def _summarize_metadata(files: dict[str, bytes], idx: dict[str, str], target: str | None, ctx: _Ctx, src: str) -> None:
    """O'chirishdan oldin metadatada nima borligini hisobotga yozamiz (muallif, kompaniya...)."""
    if not target or target.lower() not in idx:
        return
    try:
        root = parse_xml(files[idx[target.lower()]], target)
    except Rejected:
        return
    wanted = {"creator": "muallif", "lastmodifiedby": "oxirgi o'zgartirgan", "company": "kompaniya", "manager": "menejer"}
    parts = []
    for el in root.iter():
        key = _local(el).lower()
        if key in wanted and el.text and el.text.strip():
            parts.append(f"{wanted[key]}: {clean_text(el.text, 40)}")
    if parts:
        ctx.report.add("metadata", "low", target, "; ".join(parts[:4]), "removed")


def _plan(files: dict[str, bytes], idx: dict[str, str], cts: _ContentTypes, main: str, ctx: _Ctx):
    kept: dict[str, None] = {main: None}
    rels_out: dict[str, list[_Rel]] = {}
    dropped_ids: dict[str, set[str]] = {}
    dropped_targets: set[str] = set()
    queue: deque[str] = deque(["", main])
    done: set[str] = set()

    while queue:
        src = queue.popleft()
        if src in done:
            continue
        done.add(src)
        rels_file = idx.get(_rels_name(src).lower())
        if rels_file is None:
            continue
        keep_list: list[_Rel] = []
        drop: set[str] = set()
        loc = src or "paket"
        for rel in _parse_rels(files[rels_file], rels_file):
            short = rel.type.rsplit("/", 1)[-1].lower()
            rule = _REL_RULES.get(short)
            target: str | None = None
            if not rel.external:
                resolved = _resolve(src, rel.target)
                if resolved and resolved.lower() in idx:
                    target = idx[resolved.lower()]

            if rule:
                cat, sev, label = rule
                if cat == "metadata":
                    _summarize_metadata(files, idx, target, ctx, src)
                detail = f"{label}: {defang(rel.target)}" if rel.external else f"{label}: {clean_text(rel.target, 80)}"
                ctx.report.add(cat, sev, loc, detail, "removed")
                if short in ("xlmacrosheet", "xlintlmacrosheet"):
                    ctx.macro_sheets_removed = True
                if target:
                    dropped_targets.add(target)
                drop.add(rel.id)
                continue

            if rel.external:
                if short == "hyperlink" and _is_safe_url(rel.target):
                    ctx.kept_links.append(rel.target)
                    keep_list.append(rel)
                else:
                    sev = "medium" if short == "hyperlink" else "high"
                    ctx.report.add("external_link", sev, loc, f"Tashqi havola ({clean_text(short, 30)}): {defang(rel.target)}", "removed")
                    drop.add(rel.id)
                continue

            if target is None:
                ctx.report.add("broken_reference", "low", loc, f"Mavjud bo'lmagan faylga havola: {clean_text(rel.target, 80)}", "removed")
                drop.add(rel.id)
                continue

            if target != main:
                verdict = _classify_part(target, cts.content_type(target), files[target])
                if verdict:
                    cat, sev, label = verdict
                    ctx.report.add(cat, sev, target, label, "removed")
                    dropped_targets.add(target)
                    drop.add(rel.id)
                    continue
            keep_list.append(rel)
            if target not in kept:
                kept[target] = None
            queue.append(target)

        rels_out[src] = keep_list
        if drop:
            dropped_ids[src] = drop

    # Havola qilinmagan (orphan) qismlar
    benign_orphans = 0
    for name in files:
        if name == CT_CT or name.lower().endswith(".rels") or name in kept or name in dropped_targets:
            continue
        verdict = _classify_part(name, cts.content_type(name), files[name])
        if verdict and verdict[1] in ("high", "medium"):
            ctx.report.add(verdict[0], verdict[1], name, f"{verdict[2]} (hech qayerdan havola qilinmagan)", "removed")
        else:
            benign_orphans += 1
    if benign_orphans:
        ctx.report.add("orphan_part", "low", "paket", f"{benign_orphans} ta bog'lanmagan fayl o'chirildi", "removed")
    return kept, rels_out, dropped_ids


# --------------------------------------------------------------------------- 2-bosqich: qismlarni tozalash
def _strip_dropped_refs(root: etree._Element, drop_ids: set[str]) -> bool:
    changed = False
    to_remove: list[etree._Element] = []
    to_unwrap: list[etree._Element] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr, val in list(el.attrib.items()):
            if not attr.startswith("{"):
                continue
            ns, local = attr[1:].split("}", 1)
            if ns in R_NAMESPACES and val in drop_ids:
                if local == "link":  # masalan <a:blip r:link=...>: elementni saqlab, havolani olamiz
                    del el.attrib[attr]
                    changed = True
                elif _local(el) == "hyperlink":
                    to_unwrap.append(el)
                else:
                    to_remove.append(el)
                break
    for el in to_unwrap:
        _unwrap(el)
        changed = True
    for el in to_remove:
        _remove(el)
        changed = True
    return changed


def _prune_empty(root: etree._Element, names: set[str]) -> bool:
    changed = False
    for el in list(root.iter()):
        if isinstance(el.tag, str) and _local(el) in names and len(el) == 0 and not (el.text or "").strip():
            _remove(el)
            changed = True
    return changed


def _scrub_word_fields(root: etree._Element, part: str, ctx: _Ctx) -> bool:
    """DDE/INCLUDETEXT/LINK/MACROBUTTON kabi xavfli maydonlarni (field) olib tashlaydi."""
    stack: list[dict] = []
    bad: list[tuple[list[etree._Element], str]] = []
    simple: list[tuple[etree._Element, str]] = []

    def finish(f: dict) -> None:
        text = "".join(f["instr"])
        if _WORD_DANGEROUS_FIELD.match(text):
            bad.append((f["nodes"], text))

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        ln = _local(el)
        if ln == "fldChar":
            ftype = next((v for k, v in el.attrib.items() if k.endswith("}fldCharType")), None)
            if ftype == "begin":
                stack.append({"nodes": [el], "instr": [], "phase": "instr"})
            elif ftype == "separate" and stack:
                stack[-1]["nodes"].append(el)
                stack[-1]["phase"] = "result"
            elif ftype == "end" and stack:
                f = stack.pop()
                f["nodes"].append(el)
                finish(f)
        elif ln == "instrText" and stack and stack[-1]["phase"] == "instr":
            stack[-1]["nodes"].append(el)
            stack[-1]["instr"].append(el.text or "")
        elif ln == "fldSimple":
            instr = next((v for k, v in el.attrib.items() if k.endswith("}instr")), "")
            if _WORD_DANGEROUS_FIELD.match(instr):
                simple.append((el, instr))
    while stack:
        finish(stack.pop())

    for nodes, text in bad:
        for n in nodes:
            _remove(n)
        ctx.report.add("dde_field", "high", part, f"Xavfli Word maydoni: {defang(text, 80)}", "neutralized")
    for el, text in simple:
        _unwrap(el)
        ctx.report.add("dde_field", "high", part, f"Xavfli Word maydoni: {defang(text, 80)}", "neutralized")
    return bool(bad or simple)


def _scrub_worksheet(root: etree._Element, part: str, ctx: _Ctx) -> bool:
    fs = list(root.iter(*[f"{{{ns}}}f" for ns in SS_NS]))
    bad: list[etree._Element] = []
    shared_dead: set[str] = set()
    for f in fs:
        text = f.text or ""
        why = None
        if _XL_EXTREF.search(text):
            why = "tashqi fayl havolasi"
        elif _XL_DANGEROUS_FN.search(text):
            why = "xavfli funksiya"
        elif _XL_DDE.match(text):
            why = "DDE"
        if why:
            bad.append(f)
            if f.get("t") == "shared" and f.get("si") is not None:
                shared_dead.add(f.get("si"))
            ctx.report.add("formula", "high", part, f"{why}: {defang(text, 80)}", "neutralized")
    if shared_dead:
        for f in fs:
            if f.get("t") == "shared" and f.get("si") in shared_dead and f not in bad:
                bad.append(f)
    for f in bad:
        _remove(f)  # keshlangan <v> qiymati qoladi
    pruned = _prune_empty(root, {"oleObjects", "controls", "hyperlinks"})
    return bool(bad) or pruned


def _scrub_workbook(root: etree._Element, part: str, ctx: _Ctx) -> bool:
    changed = False
    for el in list(root.iter(*[f"{{{ns}}}externalReferences" for ns in SS_NS])):
        _remove(el)
        changed = True
    for el in list(root.iter(*[f"{{{ns}}}definedName" for ns in SS_NS])):
        text = el.text or ""
        name = el.get("name") or ""
        if _XL_EXTREF.search(text) or _XL_AUTO_NAMES.search(name) or (
            ctx.macro_sheets_removed and el.get("localSheetId") is not None
        ):
            ctx.report.add("formula", "high", part, f"Xavfli nom o'chirildi: {defang(name, 60)}", "neutralized")
            _remove(el)
            changed = True
    if ctx.macro_sheets_removed:
        for el in root.iter(*[f"{{{ns}}}workbookView" for ns in SS_NS]):
            for attr in ("activeTab", "firstSheet"):
                if attr in el.attrib:
                    el.set(attr, "0")
                    changed = True
    if _prune_empty(root, {"definedNames"}):
        changed = True
    return changed


def _scrub_part(name: str, root: etree._Element, drop_ids: set[str], ctx: _Ctx) -> bool:
    changed = False
    if drop_ids and _strip_dropped_refs(root, drop_ids):
        changed = True
        changed |= _prune_empty(root, {"oleObjects", "controls", "externalReferences", "hyperlinks"})
    if ctx.kind == "docx":
        changed |= _scrub_word_fields(root, name, ctx)
    elif ctx.kind == "xlsx":
        ln = _local(root)
        if ln == "worksheet":
            changed |= _scrub_worksheet(root, name, ctx)
        elif ln == "workbook":
            changed |= _scrub_workbook(root, name, ctx)
    return changed


# --------------------------------------------------------------------------- 3-bosqich: qayta qurish
def _serialize_rels(rels: list[_Rel]) -> bytes:
    root = etree.Element(f"{{{NS_PKG_REL}}}Relationships", nsmap={None: NS_PKG_REL})
    for rel in rels:
        el = etree.SubElement(root, f"{{{NS_PKG_REL}}}Relationship")
        el.set("Id", rel.id)
        el.set("Type", rel.type)
        el.set("Target", rel.target)
        if rel.external:
            el.set("TargetMode", "External")
    return _serialize(root)


def _build_content_types(names: list[str], cts: _ContentTypes, main: str, main_ct: str) -> bytes:
    defaults: dict[str, str] = {"rels": CT_RELS, "xml": "application/xml"}
    overrides: dict[str, str] = {"/" + main: main_ct}
    for name in names:
        if name == main or name.lower().endswith(".rels"):
            continue
        ct = cts.content_type(name)
        if ct is None:
            continue
        if "/" + name.lower() in cts.overrides:
            overrides["/" + name] = ct
        else:
            base = posixpath.basename(name)
            ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
            defaults[ext] = ct
    root = etree.Element(f"{{{NS_CT}}}Types", nsmap={None: NS_CT})
    for ext, ct in sorted(defaults.items()):
        etree.SubElement(root, f"{{{NS_CT}}}Default", Extension=ext, ContentType=ct)
    for part, ct in sorted(overrides.items()):
        etree.SubElement(root, f"{{{NS_CT}}}Override", PartName=part, ContentType=ct)
    return _serialize(root)


def _write_zip(parts: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    order = [CT_CT, "_rels/.rels"] + [n for n in parts if n not in (CT_CT, "_rels/.rels")]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in order:
            if name not in parts:
                continue
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))  # vaqt izi qoldirmaymiz
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zf.writestr(info, parts[name])
    return buf.getvalue()


def _rebuild(files: dict[str, bytes], report: Report) -> tuple[bytes, str]:
    idx = {n.lower(): n for n in files}
    cts = _parse_content_types(files)
    main = _find_main(files, idx)
    main_ct_raw = cts.content_type(main) or ""
    info = _MAIN_CT.get(main_ct_raw.lower())
    if info is None:
        raise Rejected("Faqat DOCX va XLSX qo'llab-quvvatlanadi (bu paket boshqa turdagi hujjat).")
    kind, converted_ct = info
    main_ct = converted_ct or main_ct_raw
    if converted_ct:
        report.converted_from = "makro-yoqilgan/shablon formati → oddiy " + kind
        report.add("macro_format", "medium", main, "Fayl makro-yoqilgan/shablon turida edi, oddiy formatga o'tkazildi", "neutralized")

    ctx = _Ctx(report=report, kind=kind)
    kept, rels_out, dropped_ids = _plan(files, idx, cts, main, ctx)

    out: dict[str, bytes] = {}
    for name in kept:
        data = files[name]
        if _is_xml_ct(cts.content_type(name)):
            root = parse_xml(data, name)
            if _scrub_part(name, root, dropped_ids.get(name, set()), ctx):
                data = _serialize(root)
        out[name] = data
    for src, rels in rels_out.items():
        if rels:
            out[_rels_name(src)] = _serialize_rels(rels)
    if "_rels/.rels" not in out:
        raise Rejected("Tozalashdan keyin hujjat bo'sh qoldi.")
    out[CT_CT] = _build_content_types(list(kept), cts, main, main_ct)

    if ctx.kept_links:
        shown = ", ".join(defang(u, 50) for u in ctx.kept_links[:5])
        more = f" (+{len(ctx.kept_links) - 5} ta)" if len(ctx.kept_links) > 5 else ""
        report.add("hyperlink", "info", "paket", f"{len(ctx.kept_links)} ta oddiy veb-havola saqlandi: {shown}{more}", "kept")
    return _write_zip(out), kind


# --------------------------------------------------------------------------- 4-bosqich: tekshiruv
def _open_with_library(clean: bytes, kind: str) -> None:
    try:
        if kind == "docx":
            import docx  # python-docx

            docx.Document(io.BytesIO(clean))
        else:
            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(clean), read_only=True)
            _ = wb.sheetnames
            wb.close()
    except Exception as exc:  # noqa: BLE001 - istalgan xato = tekshiruv muvaffaqiyatsiz
        raise VerificationFailed(f"Tozalangan fayl {kind} kutubxonasida ochilmadi: {type(exc).__name__}") from exc


def verify_ooxml(clean: bytes, kind: str, limits: ZipLimits) -> None:
    """Tozalangan faylni tekshiradi. Muvaffaqiyatsiz bo'lsa VerificationFailed (foydalanuvchiga fayl berilmaydi)."""
    try:
        files = safe_read_all(clean, limits)
        cts = _parse_content_types(files)
        idx = {n.lower(): n for n in files}
        for name in files:
            if name != CT_CT and cts.content_type(name) is None:
                raise VerificationFailed(f"Kontent turi yo'q: {clean_text(name, 60)}")
            low = name.lower()
            if low.endswith(".rels"):
                src = "" if low == "_rels/.rels" else posixpath.dirname(posixpath.dirname(name)) + "/" + posixpath.basename(name)[: -len(".rels")]
                src = src.lstrip("/")
                for rel in _parse_rels(files[name], name):
                    if not rel.external:
                        target = _resolve(src, rel.target)
                        if not target or target.lower() not in idx:
                            raise VerificationFailed("Tozalangan faylda buzilgan ichki havola qoldi.")
            elif _is_xml_ct(cts.content_type(name)):
                parse_xml(files[name], name)
        # Idempotentlik: ikkinchi o'tishda hech narsa topilmasligi kerak
        second = Report()
        _rebuild(files, second)
        leftovers = [f for f in second.findings if f.severity != "info"]
        if leftovers:
            raise VerificationFailed(f"Qayta skanerlashda {len(leftovers)} ta xavfli element qoldi.")
    except Rejected as exc:
        raise VerificationFailed(f"Tozalangan fayl yaroqsiz: {exc}") from exc
    _open_with_library(clean, kind)


def sanitize_ooxml(data: bytes, limits: ZipLimits | None = None) -> tuple[bytes, str, Report]:
    limits = limits or ZipLimits()
    report = Report()
    files = safe_read_all(data, limits)
    clean, kind = _rebuild(files, report)
    verify_ooxml(clean, kind, limits)
    report.fmt = kind
    report.verified = True
    return clean, kind, report
