import logging

import pytest
from cryptography.fernet import Fernet

from bot.config import ConfigError, load_settings
from bot.logging_setup import JsonFormatter, RedactFilter
from bot.security import SlidingWindowLimiter, output_filename, split_ext
from .conftest import make_env


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_limiter_blocks_then_recovers():
    c = Clock()
    lim = SlidingWindowLimiter(3, 10, clock=c)
    assert [lim.hit(1) for _ in range(3)] == [0, 0, 0]
    assert lim.hit(1) > 0
    assert lim.hit(2) == 0  # boshqa foydalanuvchiga ta'sir qilmaydi
    c.t = 10.5
    assert lim.hit(1) == 0


def test_limiter_memory_is_bounded():
    c = Clock()
    lim = SlidingWindowLimiter(1, 1, clock=c, max_keys=100)
    for i in range(500):
        c.t += 2
        lim.hit(i)
    assert len(lim._events) <= 101


@pytest.mark.parametrize("raw,stem,ext", [
    ("report.PDF", "report", "pdf"),
    ("../../etc/passwd.docx", "passwd", "docx"),
    ("C:\\Users\\x\\a b.xlsx", "a b", "xlsx"),
    ("evil\u202egnp.exe.pdf", "evilgnp_exe", "pdf"),
    ("noext", "noext", ""),
    (None, "document", ""),
    ("a;rm -rf $(x).pdf", "a_rm -rf __x", "pdf"),
])
def test_split_ext(raw, stem, ext):
    assert split_ext(raw) == (stem, ext)


def test_output_filename_is_safe_and_length_limited():
    assert output_filename("x" * 500 + ".docm", "docx") == "x" * 80 + "_clean.docx"
    assert output_filename("a/b.pdf", "pdf") == "b_clean.pdf"


def test_config_ok_and_derived_values():
    s = load_settings(make_env())
    assert s.admin_ids == {1001, 1002}
    assert s.webhook_url.startswith("https://bot.example.com/webhook/")
    assert len(s.webhook_path) == len("/webhook/") + 24


@pytest.mark.parametrize("over,needle", [
    ({"BOT_TOKEN": "bad"}, "BOT_TOKEN"),
    ({"WEBHOOK_BASE_URL": "http://insecure.example.com"}, "https"),
    ({"WEBHOOK_SECRET": "short"}, "WEBHOOK_SECRET"),
    ({"WEBHOOK_SECRET": "bad secret!" * 5}, "WEBHOOK_SECRET"),
    ({"WORKER_TOKEN": "x"}, "WORKER_TOKEN"),
    ({"DATA_KEY": "nope"}, "DATA_KEY"),
    ({"ADMIN_IDS": "@username"}, "username"),
    ({"ADMIN_IDS": ""}, "ADMIN_IDS"),
    ({"MAX_FILE_MB": "999"}, "MAX_FILE_MB"),
])
def test_config_rejects_bad_values(over, needle):
    with pytest.raises(ConfigError) as e:
        load_settings(make_env(**over))
    assert needle in str(e.value)


def test_logs_never_contain_secrets(capsys):
    token = "123456789:" + "B" * 35
    key = Fernet.generate_key().decode()
    logger = logging.getLogger("redact-test")
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    h.addFilter(RedactFilter([key]))
    logger.handlers[:] = [h]
    logger.propagate = False
    logger.warning("token=%s key=%s", token, key, extra={"detail": f"x {token}"})
    try:
        raise ValueError(token)
    except ValueError:
        logger.exception("boom")
    err = capsys.readouterr().err
    assert token not in err and key not in err
    assert "[REDACTED" in err
