"""Tasodifiy sirlarni yaratadi. Natijani .env ga qo'lda ko'chiring (hech qayerga yubormang)."""
import secrets

from cryptography.fernet import Fernet

print(f"WEBHOOK_SECRET={secrets.token_urlsafe(32)}")
print(f"WORKER_TOKEN={secrets.token_urlsafe(32)}")
print(f"DATA_KEY={Fernet.generate_key().decode()}")
