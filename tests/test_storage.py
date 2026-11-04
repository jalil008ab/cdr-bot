import sqlite3

from cryptography.fernet import Fernet

from bot.storage import Database


def make_db(tmp_path=None):
    path = ":memory:" if tmp_path is None else str(tmp_path / "t.db")
    return Database(path, Fernet.generate_key())


def test_sensitive_fields_encrypted_at_rest(tmp_path):
    db = make_db(tmp_path)
    jid = db.record_job(5, "pdf", "ok", "a" * 64, "b" * 64, 3, "maxfiy-shartnoma.pdf", {"x": "maxfiy-hisobot"})
    raw = sqlite3.connect(str(tmp_path / "t.db")).execute("SELECT filename_enc, report_enc FROM jobs").fetchone()
    assert b"maxfiy" not in raw[0] and b"maxfiy" not in raw[1]
    assert db.get_job_filename(jid) == "maxfiy-shartnoma.pdf"
    db.close()


def test_sql_injection_payloads_are_inert():
    db = make_db()
    evil = "x'); DROP TABLE users;--.pdf"
    db.touch_user(7)
    db.record_job(7, "pdf", "ok", None, None, 0, evil, None)
    assert db.get_job_filename(1) == evil
    assert db.stats()["users"] == 1  # jadval hali joyida


def test_ban_and_unban():
    db = make_db()
    assert not db.is_banned(9)
    db.set_banned(9, True)
    assert db.is_banned(9)
    db.set_banned(9, False)
    assert not db.is_banned(9)


def test_audit_chain_detects_tampering(tmp_path):
    db = make_db(tmp_path)
    for i in range(5):
        db.audit(1001, "ban", str(i), "")
    assert db.verify_audit_chain() == (True, None)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute("UPDATE audit SET target='999' WHERE id=3")
    conn.commit()
    assert db.verify_audit_chain() == (False, 3)
    # yozuv o'chirilsa ham ushlanadi
    conn.execute("DELETE FROM audit WHERE id=2")
    conn.commit()
    ok, bad = db.verify_audit_chain()
    assert not ok


def test_retention_purges_jobs_but_keeps_audit():
    db = make_db()
    db.record_job(1, "pdf", "ok", None, None, 0, "a.pdf", None)
    db._conn.execute("UPDATE jobs SET ts = ts - 40*86400")
    db.audit(1001, "stats")
    assert db.purge_old(30) == 1
    assert db.stats()["jobs"] == 0
    assert len(db.recent_audit()) == 1
