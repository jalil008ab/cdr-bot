import io

import pikepdf
import pytest

from worker.errors import Rejected
from worker.pdf_sanitizer import sanitize_pdf
from . import samples


def test_malicious_pdf_is_disarmed():
    data = samples.make_malicious_pdf()
    clean, rep = sanitize_pdf(data)
    assert rep.verified
    for needle in (b"JavaScript", b"/JS", b"OpenAction", b"/Launch", b"EmbeddedFile", b"payload.exe", b"cmd.exe", b"file:///etc/passwd", b"Ali Valiyev", b"/Screen"):
        assert needle not in clean, needle
    assert b"https://example.com" in clean  # xavfsiz URI qoladi
    cats = {f.category for f in rep.findings}
    assert {"auto_action", "javascript", "embedded_file", "action", "annotation", "metadata"} <= cats
    with pikepdf.open(io.BytesIO(clean)) as pdf:
        assert len(pdf.pages) == 1
        assert "/Info" not in pdf.trailer


def test_clean_pdf_is_stable():
    clean, rep = sanitize_pdf(samples.make_plain_pdf())
    assert rep.removed_count == 0
    again, rep2 = sanitize_pdf(clean)
    assert rep2.removed_count == 0
    with pikepdf.open(io.BytesIO(again)) as pdf:
        assert len(pdf.pages) == 2


def test_rejects_non_pdf_and_garbage():
    with pytest.raises(Rejected):
        sanitize_pdf(b"not a pdf at all")
    with pytest.raises(Rejected):
        sanitize_pdf(b"%PDF-1.7\n" + b"garbage" * 100)


def test_rejects_password_protected():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(user="secret", owner="owner"))
    with pytest.raises(Rejected):
        sanitize_pdf(buf.getvalue())


def test_chained_actions_removed():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    nxt = pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("x"))
    goto = pikepdf.Dictionary(S=pikepdf.Name.GoTo, D=pikepdf.Array([pdf.pages[0].obj, pikepdf.Name.Fit]), Next=nxt)
    pdf.pages[0].obj.Annots = pikepdf.Array([pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 5, 5], A=goto))])
    buf = io.BytesIO()
    pdf.save(buf)
    clean, rep = sanitize_pdf(buf.getvalue())
    assert b"JavaScript" not in clean and b"/JS" not in clean
