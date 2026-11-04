import pytest
from cryptography.fernet import Fernet

from bot.config import Settings, load_settings


def make_env(**over):
    env = {
        "BOT_TOKEN": "123456789:" + "A" * 35,  # dinamik: repoda haqiqiy ko'rinishdagi token bo'lmasin
        "WEBHOOK_BASE_URL": "https://bot.example.com",
        "WEBHOOK_SECRET": "s" * 40,
        "WORKER_URL": "http://127.0.0.1:1",
        "WORKER_TOKEN": "w" * 40,
        "DATA_KEY": Fernet.generate_key().decode(),
        "ADMIN_IDS": "1001,1002",
        "DB_PATH": ":memory:",
    }
    env.update(over)
    return env


@pytest.fixture
def settings() -> Settings:
    return load_settings(make_env())
