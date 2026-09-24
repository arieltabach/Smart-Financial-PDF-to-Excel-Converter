import re

import pytest

from converter.anonymizer import Anonymizer

SAMPLE = """Account holder: Israel Israeli
לכבוד: ישראל ישראלי
כתובת: הרצל 12, תל אביב
Account 12-345-678901 IBAN IL620108000000099999999 SWIFT: POALILIT
Card 4580 1234 5678 9012 / **** 9012  Phone 054-1234567  ID 305123456 mail israel.i@gmail.com
03/02/2025 Salary - ACME LTD TRF 88213 12,500.00 14,793.10
12/02/2025 Rent - February STO 7 4,300.00 9,370.74  ref 20250212001
Date: 01/02/2025 | Description: Opening balance | Reference: - | Debit: - | Credit: - | Balance: 2,450.00
"""


@pytest.fixture
def masked():
    a = Anonymizer(extra_terms=["ACME LTD"], salt="test")
    return a, a.mask(SAMPLE)


def test_sensitive_values_are_gone(masked):
    _, m = masked
    for secret in ("Israel Israeli", "ישראל ישראלי", "הרצל 12", "12-345-678901", "IL620108000000099999999",
                   "POALILIT", "4580 1234 5678 9012", "**** 9012", "054-1234567", "305123456",
                   "israel.i@gmail.com", "ACME LTD", "20250212001"):
        assert secret not in m, secret


def test_amounts_dates_and_short_refs_untouched(masked):
    _, m = masked
    for keep in ("03/02/2025", "12/02/2025", "01/02/2025", "12,500.00", "14,793.10", "4,300.00", "9,370.74",
                 "2,450.00", "TRF 88213", "STO 7", "Rent - February", "Opening balance"):
        assert keep in m, keep
    # the labelled table row is a header-like line and must not be treated as an identity
    assert "Date: 01/02/2025 | Description: Opening balance" in m


def test_shape_is_preserved(masked):
    a, m = masked
    fake = a.mapping["12-345-678901"]
    assert re.fullmatch(r"\d{2}-\d{3}-\d{6}", fake) and fake != "12-345-678901"
    assert re.fullmatch(r"\d{4} \d{4} \d{4} \d{4}", a.mapping["4580 1234 5678 9012"])
    assert re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{19}", a.mapping["IL620108000000099999999"])
    assert a.mapping["israel.i@gmail.com"].endswith("@masked.example")
    assert a.mapping["ACME LTD"].startswith("Customer ")
    assert a.mapping["ישראל ישראלי"].startswith("לקוח ")


def test_round_trip_restore(masked):
    a, m = masked
    assert a.restore(m) == SAMPLE


def test_restore_rows_and_separator_stripped_variant(masked):
    a, _ = masked
    fake_acct = a.mapping["12-345-678901"]
    rows = [{"description": f"Salary - {a.mapping['ACME LTD']}", "reference": fake_acct.replace("-", ""), "debit": "1", "_page": 1}]
    out = a.restore_rows(rows)
    assert out[0]["description"] == "Salary - ACME LTD"
    assert out[0]["reference"] == "12345678901"
    assert out[0]["_page"] == 1


def test_deterministic_within_instance_and_across_pages():
    a = Anonymizer(salt="x")
    p1 = a.mask("acct 12-345-678901 on page one")
    p2 = a.mask("again 12-345-678901 on page two")
    assert a.mapping["12-345-678901"] in p1 and a.mapping["12-345-678901"] in p2
    assert a.report.counts["account/card numbers"] == 1


def test_different_documents_get_different_fakes():
    assert Anonymizer(salt="a").mask("305123456") != Anonymizer(salt="b").mask("305123456")


def test_no_double_masking_of_generated_fakes():
    a = Anonymizer(salt="x")
    m = a.mask("Account 12-345-678901")
    assert len(a.mapping) == 1
    assert m == "Account " + a.mapping["12-345-678901"]


def test_report_summary(masked):
    a, _ = masked
    assert a.report.total >= 10
    assert "account/card numbers" in a.report.summary()
    assert Anonymizer().report.summary() == "nothing to mask"


def test_big_unformatted_amount_is_protected():
    a = Anonymizer(salt="x")
    assert a.mask("total 1234567.89 paid") == "total 1234567.89 paid"
    assert a.mask("iso 2025-02-03 date") == "iso 2025-02-03 date"
