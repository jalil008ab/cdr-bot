from bot.texts import format_report, safe_text


def test_safe_text_strips_bidi_and_controls():
    assert safe_text("a\u202eb\x00c\n d") == "abc d"


def test_report_is_bounded_and_sanitized():
    report = {
        "fmt": "docx", "input_size": 2048, "output_size": 1024, "input_sha256": "a" * 64, "output_sha256": "b" * 64,
        "verified": True, "truncated": 5,
        "findings": [{"severity": "high", "detail": "evil\u202e" + "x" * 1000, "location": "p", "action": "removed"}] * 300,
    }
    text = format_report(report)
    assert len(text) <= 3800
    assert "\u202e" not in text
    assert "yana" in text
