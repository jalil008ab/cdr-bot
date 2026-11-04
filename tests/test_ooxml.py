import io
import zipfile

import pytest

from worker.errors import Rejected
from worker.ooxml import sanitize_ooxml
from . import samples
from .test_zipsafe import zip_bytes


def names(data):
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def cats(report):
    return {f.category for f in report.findings}


def test_malicious_docx_is_disarmed():
    clean, kind, rep = sanitize_ooxml(samples.make_malicious_docx())
    assert kind == "docx" and rep.verified
    ns = names(clean)
    assert not any("vbaProject" in n or "embeddings" in n or n.startswith("docProps") for n in ns)
    assert {"macro", "ole", "remote_template", "dde_field", "metadata", "suspicious_part"} <= cats(rep)
    joined = b"".join(zipfile.ZipFile(io.BytesIO(clean)).read(n) for n in ns)
    assert b"evil.example.com" not in joined
    assert b"DDEAUTO" not in joined and b"calc.exe" not in joined and b"/c calc" not in joined and b"instrText" not in joined
    assert "DDE natija".encode() in joined  # maydon natijasi (oddiy matn) saqlanadi
    assert b"javascript:" not in joined
    assert b"https://example.com/page" in joined  # oddiy havola saqlanadi
    assert "docm" not in rep.converted_from and rep.converted_from  # makro-yoqilgan -> oddiy
    assert b"macroEnabled" not in zipfile.ZipFile(io.BytesIO(clean)).read("[Content_Types].xml")


def test_report_defangs_urls_and_has_metadata_info():
    _, _, rep = sanitize_ooxml(samples.make_malicious_docx())
    tpl = next(f for f in rep.findings if f.category == "remote_template")
    assert "hxxp" in tpl.detail and "http://" not in tpl.detail
    meta = next(f for f in rep.findings if f.category == "metadata" and "muallif" in f.detail)
    assert "Ali Valiyev" in meta.detail


def test_clean_docx_roundtrip_is_idempotent():
    clean, _, rep = sanitize_ooxml(samples.make_plain_docx())
    again, _, rep2 = sanitize_ooxml(clean)
    assert [f for f in rep2.findings if f.severity != "info"] == []
    assert rep.removed_count >= 1  # metadata o'chirildi
    from docx import Document
    assert Document(io.BytesIO(again)).paragraphs[0].text == "Salom, bu oddiy hujjat."


def test_malicious_xlsx_is_disarmed():
    clean, kind, rep = sanitize_ooxml(samples.make_malicious_xlsx())
    assert kind == "xlsx" and rep.verified
    ns = names(clean)
    assert not any("vbaProject" in n or "externalLink" in n or "macrosheet" in n for n in ns)
    wb = zipfile.ZipFile(io.BytesIO(clean)).read("xl/workbook.xml")
    assert b"externalReferences" not in wb and b"Macro1" not in wb
    sheet = zipfile.ZipFile(io.BytesIO(clean)).read("xl/worksheets/sheet1.xml")
    assert b"EXEC" not in sheet and b"cmd|" not in sheet and b"[1]" not in sheet
    assert b"SUM(A1,5)" in sheet  # oddiy formula saqlanadi
    assert {"macro", "external_link", "formula", "metadata"} <= cats(rep)
    import openpyxl
    wb2 = openpyxl.load_workbook(io.BytesIO(clean))
    assert wb2.active["A1"].value == 10


def test_rejects_non_ooxml_zip():
    with pytest.raises(Rejected):
        sanitize_ooxml(zip_bytes({"hello.txt": b"hi"}))


def test_rejects_xxe_doctype():
    parts = samples.read_zip(samples.make_plain_docx())
    parts["word/document.xml"] = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>&x;</w:body></w:document>'
    )
    with pytest.raises(Rejected):
        sanitize_ooxml(samples.write_zip(parts))


def test_rejects_billion_laughs():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]><r>&lol3;</r>'
    )
    parts = samples.read_zip(samples.make_plain_docx())
    parts["word/document.xml"] = bomb
    with pytest.raises(Rejected):
        sanitize_ooxml(samples.write_zip(parts))


def test_spoofed_image_is_dropped():
    parts = samples.read_zip(samples.make_plain_docx())
    parts["word/media/evil.png"] = b"MZ\x90\x00 this is not a png"
    samples.add_rel(parts, "word/_rels/document.xml.rels", "rIdImg", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", "media/evil.png")
    ct = parts["[Content_Types].xml"].decode()
    if 'Extension="png"' not in ct:
        ct = ct.replace("<Override", '<Default Extension="png" ContentType="image/png"/><Override', 1)
    parts["[Content_Types].xml"] = ct.encode()
    clean, _, rep = sanitize_ooxml(samples.write_zip(parts))
    assert "word/media/evil.png" not in names(clean)
    assert "image_spoof" in cats(rep)
