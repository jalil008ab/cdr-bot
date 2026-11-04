"""Telegram tarmog'isiz sinash uchun soxta sessiya: yuborilgan xabarlar/hujjatlar yig'iladi."""
from __future__ import annotations

from datetime import datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import DeleteMessage, GetFile, SendDocument, SendMessage
from aiogram.types import Chat, Document, File, Message, Update, User


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.texts: list[str] = []
        self.documents: list[tuple[str, bytes, str | None]] = []
        self.files: dict[str, bytes] = {}
        self._mid = 100

    async def close(self):
        pass

    def _msg(self, chat_id: int, text: str | None = None) -> Message:
        self._mid += 1
        return Message(
            message_id=self._mid, date=datetime.now(), chat=Chat(id=chat_id, type="private"),
            from_user=User(id=1, is_bot=True, first_name="bot"), text=text,
        )

    async def make_request(self, bot: Bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.texts.append(method.text)
            return self._msg(method.chat_id, method.text)
        if isinstance(method, SendDocument):
            f = method.document
            self.documents.append((f.filename, f.data, method.caption))
            return self._msg(method.chat_id)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path=f"files/{method.file_id}")
        if isinstance(method, DeleteMessage):
            return True
        raise AssertionError(f"kutilmagan metod: {method}")

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        data = self.files[url.rsplit("/", 1)[-1]]
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]


def make_bot(token: str) -> tuple[Bot, FakeSession]:
    session = FakeSession()
    return Bot(token, session=session), session


_uid = 0


def message_update(user_id: int, text: str | None = None, document: Document | None = None, chat_type: str = "private") -> Update:
    global _uid
    _uid += 1
    msg = Message(
        message_id=_uid, date=datetime.now(), chat=Chat(id=user_id, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="t"), text=text, document=document,
    )
    return Update(update_id=_uid, message=msg)


def doc(file_id: str, name: str, size: int) -> Document:
    return Document(file_id=file_id, file_unique_id=file_id + "u", file_name=name, file_size=size)
