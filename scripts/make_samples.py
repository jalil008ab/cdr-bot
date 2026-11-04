"""Demo/sinov uchun zararsiz (soxta) namunalarni samples/ papkasiga yozadi."""
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from tests import samples  # noqa: E402

out = root / "samples"
out.mkdir(exist_ok=True)
(out / "demo_macro.docm").write_bytes(samples.make_malicious_docx())
(out / "demo_external_dde.xlsx").write_bytes(samples.make_malicious_xlsx())
(out / "demo_javascript.pdf").write_bytes(samples.make_malicious_pdf())
(out / "clean_example.docx").write_bytes(samples.make_plain_docx())
print("Yaratildi:", *sorted(p.name for p in out.iterdir()))
