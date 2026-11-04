import io
import struct
import zipfile

import pytest

from worker.errors import UnsafeArchive
from worker.zipsafe import ZipLimits, safe_read_all


def zip_bytes(entries: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def test_reads_normal_archive():
    out = safe_read_all(zip_bytes({"a.txt": b"hello", "d/b.txt": b"x"}))
    assert out == {"a.txt": b"hello", "d/b.txt": b"x"}


@pytest.mark.parametrize("name", ["../evil.txt", "/abs.txt", "a/../../b.txt", "a\\b.txt", "C:/x.txt", "./a.txt"])
def test_rejects_path_traversal_names(name):
    with pytest.raises(UnsafeArchive):
        safe_read_all(zip_bytes({name: b"x"}))


def test_rejects_zip_bomb_ratio():
    data = zip_bytes({"bomb.bin": b"\x00" * (20 * 1024 * 1024)})
    with pytest.raises(UnsafeArchive):
        safe_read_all(data, ZipLimits(max_ratio=100))


def test_rejects_total_size():
    data = zip_bytes({f"f{i}.txt": b"A" * 600_000 + bytes([i]) * 10 for i in range(5)})
    with pytest.raises(UnsafeArchive):
        safe_read_all(data, ZipLimits(max_total_uncompressed=2_000_000))


def test_rejects_too_many_entries():
    data = zip_bytes({f"f{i}.txt": b"x" for i in range(50)})
    with pytest.raises(UnsafeArchive):
        safe_read_all(data, ZipLimits(max_entries=10))


def test_rejects_duplicate_names_case_insensitive():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("A.txt", b"1")
        zf.writestr("a.txt", b"2")
    with pytest.raises(UnsafeArchive):
        safe_read_all(buf.getvalue())


def test_rejects_encrypted_flag():
    data = bytearray(zip_bytes({"a.txt": b"hello"}))
    # local header (offset 6) va central directory'dagi flag bitlarini 1 qilamiz
    data[6] |= 1
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 8] |= 1
    with pytest.raises(UnsafeArchive):
        safe_read_all(bytes(data))


def test_rejects_lying_header_size():
    data = bytearray(zip_bytes({"a.txt": b"hello world" * 10}, zipfile.ZIP_STORED))
    cd = data.rfind(b"PK\x01\x02")
    struct.pack_into("<I", data, cd + 24, 5)  # central directory'da file_size = 5 (yolg'on)
    with pytest.raises(UnsafeArchive):
        safe_read_all(bytes(data))


def test_rejects_overlapping_entries():
    data = bytearray(zip_bytes({"a.txt": b"A" * 1000, "b.txt": b"B" * 1000}, zipfile.ZIP_STORED))
    cds = [i for i in range(len(data)) if data[i:i + 4] == b"PK\x01\x02"]
    first_offset = struct.unpack_from("<I", data, cds[0] + 42)[0]
    struct.pack_into("<I", data, cds[1] + 42, first_offset)  # ikkinchi yozuv birinchisiga ishora qiladi
    with pytest.raises(UnsafeArchive):
        safe_read_all(bytes(data))


def test_rejects_garbage():
    with pytest.raises(UnsafeArchive):
        safe_read_all(b"PK\x03\x04 not really a zip")
