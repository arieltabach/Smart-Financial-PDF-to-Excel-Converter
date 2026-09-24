import pandas as pd
import pytest

from converter.data_cleaner import parse_amount, parse_date, rows_to_dataframe, validate_running_balance


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1,234.56", 1234.56),
        ("₪ 1,234.56", 1234.56),
        ("1.234,56", 1234.56),
        ("1234,56", 1234.56),
        ("1,234", 1234.0),
        ("(500.00)", -500.0),
        ("500.00-", -500.0),
        ("-500", -500.0),
        ("−12.5", -12.5),
        ("$89.99", 89.99),
        ("156.90 ש\"ח", 156.90),
        ("‏1,000.00‎", 1000.0),
        ("", None),
        (None, None),
        ("abc", None),
        (12, 12.0),
        (float("nan"), None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("03/02/2025", "2025-02-03"),
        ("3.2.25", "2025-02-03"),
        ("03-02-2025", "2025-02-03"),
        ("2025-02-03", "2025-02-03"),
        ("", None),
        ("not a date", None),
    ],
)
def test_parse_date_dayfirst(raw, expected):
    got = parse_date(raw, dayfirst=True)
    assert (got is None and expected is None) or got.strftime("%Y-%m-%d") == expected


def test_parse_date_monthfirst():
    assert parse_date("03/02/2025", dayfirst=False).strftime("%Y-%m-%d") == "2025-03-02"


def _rows():
    return [
        {"date": "01/02/2025", "description": "Opening", "reference": "", "debit": "", "credit": "", "balance": "1,000.00"},
        {"date": "02/02/2025", "description": "Coffee", "reference": "4471", "debit": "20.00", "credit": "", "balance": "980.00"},
        {"date": "03/02/2025", "description": "Salary", "reference": "", "debit": "", "credit": "5,000.00", "balance": "5,980.00"},
        {"date": "", "description": "Total", "reference": "", "debit": "", "credit": "", "balance": ""},  # noise → dropped
        {"date": "02/02/2025", "description": "Coffee", "reference": "4471", "debit": "20.00", "credit": "", "balance": "980.00"},  # dup
    ]


def test_rows_to_dataframe_shape_and_types():
    df = rows_to_dataframe(_rows())
    assert list(df.columns[:7]) == ["Date", "Description", "Reference", "Debit", "Credit", "Amount", "Balance"]
    assert len(df) == 2  # opening-balance line, noise and duplicate removed
    assert pd.api.types.is_datetime64_any_dtype(df["Date"])
    assert df["Debit"].dtype == float
    assert df.loc[0, "Amount"] == -20.0
    assert df.loc[1, "Amount"] == 5000.0


def test_opening_balance_rows_dropped_in_hebrew_too():
    rows = [
        {"date": "01/02/2025", "description": "יתרת פתיחה", "reference": "", "debit": "", "credit": "", "balance": "1,000.00"},
        {"date": "02/02/2025", "description": "קפה", "reference": "", "debit": "20", "credit": "", "balance": "980.00"},
    ]
    df = rows_to_dataframe(rows)
    assert list(df["Description"]) == ["קפה"]


def test_opening_row_kept_when_it_has_a_movement():
    rows = [{"date": "01/02/2025", "description": "Opening deposit", "reference": "", "debit": "", "credit": "100", "balance": "100"}]
    assert len(rows_to_dataframe(rows)) == 1


def test_signed_amount_in_wrong_column_is_normalised():
    df = rows_to_dataframe([{"date": "01/01/2025", "description": "x", "reference": "", "debit": "-50", "credit": "", "balance": ""}])
    assert pd.isna(df.loc[0, "Debit"])
    assert df.loc[0, "Credit"] == 50.0


def test_validate_running_balance_chronological():
    df, report = validate_running_balance(rows_to_dataframe(_rows()))
    assert report.direction == "chronological"
    assert report.n_checked == 1 and report.n_mismatch == 0 and report.ok
    assert list(df["Balance OK"]) == [None, True]
    assert report.n_autofixed == 0


def _swap_rows():
    return [
        {"date": "01/02/2025", "description": "A", "reference": "", "debit": "20", "credit": "", "balance": "980.00"},
        {"date": "02/02/2025", "description": "B (wrong column)", "reference": "", "debit": "", "credit": "74.50", "balance": "905.50"},
        {"date": "03/02/2025", "description": "C", "reference": "", "debit": "", "credit": "100", "balance": "1,005.50"},
    ]


def test_autofix_swaps_debit_credit_when_balance_proves_it():
    df, report = validate_running_balance(rows_to_dataframe(_swap_rows()))
    assert report.n_autofixed == 1 and report.ok
    assert df.loc[1, "Debit"] == 74.5 and pd.isna(df.loc[1, "Credit"]) and df.loc[1, "Amount"] == -74.5
    assert report.total_debit == 94.5 and report.total_credit == 100.0


def test_autofix_can_be_disabled():
    df, report = validate_running_balance(rows_to_dataframe(_swap_rows()), autofix=False)
    assert report.n_autofixed == 0 and report.n_mismatch == 1
    assert df.loc[1, "Credit"] == 74.5


def test_autofix_does_not_touch_genuine_mismatch():
    rows = _swap_rows()
    rows[1]["balance"] = "900.00"  # neither +74.5 nor -74.5 reconciles
    rows[2]["balance"] = "1,000.00"  # consistent again from 900
    df, report = validate_running_balance(rows_to_dataframe(rows))
    assert report.n_autofixed == 0 and report.n_mismatch == 1
    assert df.loc[1, "Credit"] == 74.5


def test_validate_running_balance_reverse_order():
    clean = _rows()[:3]  # newest-first statement, no noise/dups so order is preserved
    df, report = validate_running_balance(rows_to_dataframe(clean[::-1]))
    assert report.direction == "reverse"
    assert report.ok


def test_validate_running_balance_detects_mismatch():
    rows = _rows()
    rows[2]["balance"] = "5,000.00"  # should be 5,980
    _, report = validate_running_balance(rows_to_dataframe(rows))
    assert report.n_mismatch == 1 and not report.ok


def test_validate_empty():
    df, report = validate_running_balance(rows_to_dataframe([]))
    assert df.empty and report.direction == "n/a" and report.n_rows == 0
