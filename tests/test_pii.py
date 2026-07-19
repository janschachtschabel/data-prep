"""PII engine: detection, masking vs dropping, idempotency, aggregate reports.

The engine is deliberately conservative: educational texts are full of numbers
(years, dates, ISBNs, resolutions) that must NOT count as phone numbers.
"""

from __future__ import annotations

import json

import pytest

from app.pii import PiiReport, apply, scan, scrub

# ------------------------------------------------------------------ email ----


def test_email_is_masked_and_counted():
    text, found = scrub("Kontakt: maria.mueller@schule-beispiel.de für Fragen")
    assert text == "Kontakt: [email] für Fragen"
    assert found == {"email": 1}


def test_multiple_emails_are_all_masked():
    text, found = scrub("a@x.de schreibt an b@y.org")
    assert "[email]" in text
    assert "a@x.de" not in text and "b@y.org" not in text
    assert found == {"email": 2}


# ------------------------------------------------------------------ phone ----


@pytest.mark.parametrize(
    "text",
    [
        "Ruf an: +49 30 1234567",
        "Tel. 030/123456",
        "Mobil 0171-2345678",
        "Sekretariat (030) 12 34 56",
        "Hotline 0800 111 0 111",
    ],
)
def test_phone_variants_are_masked(text):
    cleaned, found = scrub(text)
    assert "[phone]" in cleaned
    assert found.get("phone", 0) >= 1
    # No digit block of the original number survives.
    assert not any(run in cleaned for run in ("1234567", "123456", "2345678", "12 34 56", "111 0 111"))


@pytest.mark.parametrize(
    "text",
    [
        "Erschienen im Jahr 2026",
        "Auflösung 1024 x 768 Pixel",
        "Siehe Kapitel 3.4.5 im Buch",
        "Am 01.02.2026 ist der Termin",
        "Mit 0,5 Liter Wasser mischen",
        "ISBN 978-3-16-148410-0",
    ],
)
def test_common_numbers_are_not_phone_false_positives(text):
    cleaned, found = scrub(text)
    assert cleaned == text
    assert found == {}


# -------------------------------------------------------------------- url ----


def test_urls_are_masked_keeping_trailing_punctuation():
    cleaned, found = scrub("Mehr auf https://schule-x.de/kurs.")
    assert cleaned == "Mehr auf [url]."
    assert found == {"url": 1}
    cleaned, found = scrub("Siehe www.beispiel.de!")
    assert cleaned == "Siehe [url]!"
    assert found == {"url": 1}


# ----------------------------------------------------------------- handle ----


def test_social_handles_are_masked():
    cleaned, found = scrub("Folge @maria_lehrerin für Material")
    assert cleaned == "Folge [handle] für Material"
    assert found == {"handle": 1}


def test_emails_are_not_double_counted_as_handles():
    _, found = scrub("kontakt@schule.de")
    assert found == {"email": 1}


# ------------------------------------------------------- policy & behavior ----


def test_scan_reports_counts_without_modifying():
    assert scan("a@b.de und +49 30 1234567") == {"email": 1, "phone": 1}
    assert scan("Ganz sauberer Text über Photosynthese") == {}


def test_scrub_is_idempotent():
    original = "a@b.de, +49 30 1234567, https://x.de/k, @handle_x fertig"
    once, first = scrub(original)
    twice, second = scrub(once)
    assert twice == once
    assert first != {}
    assert second == {}


def test_apply_mask_keeps_row_apply_drop_removes_it():
    masked, found = apply("Schreib an a@b.de", "mask")
    assert masked == "Schreib an [email]"
    assert found == {"email": 1}

    dropped, found = apply("Schreib an a@b.de", "drop")
    assert dropped is None
    assert found == {"email": 1}

    kept, found = apply("Sauberer Text", "drop")
    assert kept == "Sauberer Text"
    assert found == {}


def test_apply_rejects_unknown_action():
    with pytest.raises(ValueError, match="action"):
        apply("x", "shred")


# ------------------------------------------------------------------ report ----


def test_report_aggregates_counts_never_plaintext():
    report = PiiReport()
    for text in ("a@b.de hier", "ganz sauber", "+49 30 1234567 anrufen"):
        _, found = scrub(text)
        report.record(found)
    assert report.rows_scanned == 3
    assert report.rows_affected == 2
    assert report.counts == {"email": 1, "phone": 1}

    serialized = json.dumps(report.as_dict())
    assert "a@b.de" not in serialized and "1234567" not in serialized
    assert json.loads(serialized) == {
        "rows_scanned": 3,
        "rows_affected": 2,
        "counts": {"email": 1, "phone": 1},
    }
