class Rejected(Exception):
    """Fayl rad etildi (zararli/yaroqsiz/qo'llab-quvvatlanmaydi). Xabar foydalanuvchiga ko'rsatilishi mumkin."""


class UnsafeArchive(Rejected):
    pass


class VerificationFailed(Exception):
    """Tozalangan fayl tekshiruvdan o'tmadi. Ichki xato: foydalanuvchiga fayl BERILMAYDI."""
