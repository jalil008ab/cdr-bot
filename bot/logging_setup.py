"""JSON loglar + sirlarni yashirish (redaction). Loglarda parol/token hech qachon chiqmasligi kerak."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_TOKEN_PATTERN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class RedactFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None):
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s and len(s) >= 8]

    def redact(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "[REDACTED]")
        return _TOKEN_PATTERN.sub("[REDACTED_TOKEN]", text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        for key, val in list(record.__dict__.items()):
            if key not in _STD and isinstance(val, str):
                record.__dict__[key] = self.redact(val)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in _STD and not key.startswith("_"):
                payload[key] = val
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(secrets: list[str] | None = None, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactFilter(secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
