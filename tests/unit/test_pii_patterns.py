from src.security.pii_patterns import mask_record, mask_text


def test_mask_record_column_denylist():
    row = {"email":"alice@example.com", "street_address":"1 Main", "total": 42}
    masked = mask_record(row)
    assert masked["email"] == "[REDACTED]"
    assert masked["street_address"] == "[REDACTED]"
    assert masked["total"] == 42


def test_mask_text_email_and_phone():
    text = "Email a@b.com or call +34 600 111 222"
    masked = mask_text(text)
    assert "a@b.com" not in masked
    assert "+34" not in masked
    assert masked.count("[REDACTED]") == 2


def test_mask_text_masks_formatted_phones():
    assert "555-123-4567" not in mask_text("Call 555-123-4567 today")
    assert "555-0100" not in mask_text("Reach us at (212) 555-0100")
    assert "+36" not in mask_text("Support line: +36 30 123 4567")


def test_mask_text_keeps_plain_analytics_numbers():
    # Bare digit runs are revenue sums / byte counts, not phone numbers.
    assert mask_text("Total revenue was 1234567890 dollars") == "Total revenue was 1234567890 dollars"
    assert mask_text("bytes_estimate: 200000000") == "bytes_estimate: 200000000"
    assert mask_text("Average basket value 123456789.5 this year") == "Average basket value 123456789.5 this year"
    assert mask_text("month 2026-01 revenue 132500.0") == "month 2026-01 revenue 132500.0"
