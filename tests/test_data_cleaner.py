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
    assert len(df) == 3  # noise + duplicate removed
    assert pd.api.types.is_datetime64_any_dtype(df["Date"])
    assert df["Debit"].dtype == float
    assert df.loc[1, "Amount"] == -20.0
    assert df.loc[2, "Amount"] == 5000.0


def test_signed_amount_in_wrong_column_is_normalised():
    df = rows_to_dataframe([{"date": "01/01/2025", "description": "x", "reference": "", "debit": "-50", "credit": "", "balance": ""}])
    assert pd.isna(df.loc[0, "Debit"])
    assert df.loc[0, "Credit"] == 50.0


def test_validate_running_balance_chronological():
    df, report = validate_running_balance(rows_to_dataframe(_rows()))
    assert report.direction == "chronological"
    assert report.n_checked == 2 and report.n_mismatch == 0 and report.ok
    assert list(df["Balance OK"]) == [None, True, True]


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
