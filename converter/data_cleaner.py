"""Turn the LLM's raw rows into a clean, validated ``pandas.DataFrame``.

* amounts → float (handles ₪/$/€, thousands separators, (parentheses),
  trailing minus, Unicode minus, Hebrew "ש\"ח")
* dates → ``datetime64`` (day-first by default, as printed on Israeli /
  European statements)
* running-balance check → flags rows whose balance does not equal
  previous balance + credit − debit
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import pandas as pd
from dateutil import parser as dateparser

COLUMNS = ["Date", "Description", "Reference", "Debit", "Credit", "Amount", "Balance"]

# Balance-only lines that are not transactions (English + Hebrew).
_NON_TXN_RE = re.compile(
    r"(?:opening|closing|previous|brought forward|carried forward|b/f|c/f|balance forward"
    r"|יתרת פתיחה|יתרה קודמת|יתרת סגירה|יתרה לתחילת|יתרה לסוף|העברה מדף קודם)",
    re.IGNORECASE,
)

_CURRENCY_RE = re.compile(r"(₪|\$|€|£|ש\"ח|ש״ח|שח|NIS|ILS|USD|EUR|GBP)", re.IGNORECASE)
_KEEP_RE = re.compile(r"[^0-9,.\-()]")
_BIDI_MARKS = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮⁦⁧⁨⁩"), None)


def parse_amount(value) -> float | None:
    """Parse a money string into a float. Returns ``None`` when empty/unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if (isinstance(value, float) and math.isnan(value)) else float(value)

    s = str(value).translate(_BIDI_MARKS).strip()
    if not s or s.lower() in {"nan", "none", "null", "-", "—"}:
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    s = s.replace("−", "-").replace("−", "-")
    if s.endswith("-"):
        negative, s = True, s[:-1]
    if s.startswith("-"):
        negative, s = True, s[1:]
    if s.endswith("CR") or s.endswith("DR"):
        s = s[:-2]

    s = _CURRENCY_RE.sub("", s)
    s = _KEEP_RE.sub("", s).strip()
    if not s:
        return None

    # Decide which symbol is the decimal separator.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # 1.234,56
        else:
            s = s.replace(",", "")  # 1,234.56
    elif "," in s:
        head, _, tail = s.rpartition(",")
        if len(tail) == 2 and head:
            s = f"{head.replace(',', '')}.{tail}"  # 1234,56
        else:
            s = s.replace(",", "")  # 1,234
    try:
        num = float(s)
    except ValueError:
        return None
    return -num if negative else num


def parse_date(value, dayfirst: bool = True) -> pd.Timestamp | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value
    s = str(value).translate(_BIDI_MARKS).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    # ISO dates (2025-02-03) are unambiguous — never apply day-first to them.
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", s):
        try:
            return pd.Timestamp(dateparser.parse(s, yearfirst=True))
        except (ValueError, OverflowError):
            return None
    # Normalise "3.2.25" / "03-02-2025" → "03/02/2025" so dateutil is happy.
    s = re.sub(r"(?<=\d)[.\-](?=\d)", "/", s)
    try:
        return pd.Timestamp(dateparser.parse(s, dayfirst=dayfirst, fuzzy=True))
    except (ValueError, OverflowError):
        return None


def _clean_text(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).translate(_BIDI_MARKS)).strip()


def rows_to_dataframe(rows: list[dict], dayfirst: bool = True) -> pd.DataFrame:
    """Convert raw LLM rows into the canonical 7-column DataFrame."""
    records = []
    for r in rows:
        debit = parse_amount(r.get("debit"))
        credit = parse_amount(r.get("credit"))
        # Models sometimes emit a signed number in the wrong column; normalise.
        if debit is not None and debit < 0:
            credit, debit = (credit or 0) + (-debit), None
        if credit is not None and credit < 0:
            debit, credit = (debit or 0) + (-credit), None
        debit = abs(debit) if debit is not None else None
        credit = abs(credit) if credit is not None else None

        records.append(
            {
                "Date": parse_date(r.get("date"), dayfirst=dayfirst),
                "Description": _clean_text(r.get("description")),
                "Reference": _clean_text(r.get("reference")),
                "Debit": debit,
                "Credit": credit,
                "Amount": (credit or 0.0) - (debit or 0.0) if (debit is not None or credit is not None) else None,
                "Balance": parse_amount(r.get("balance")),
                "Page": r.get("_page"),
            }
        )

    df = pd.DataFrame.from_records(records, columns=COLUMNS + ["Page"])
    if df.empty:
        return df

    # Drop rows that carry no useful information (header/total noise).
    has_money = df[["Debit", "Credit", "Balance"]].notna().any(axis=1)
    has_text = df["Description"].str.len() > 0
    df = df[has_money & (has_text | df["Date"].notna())]
    # Opening/closing balance lines carry a balance but no movement.
    balance_only = df["Debit"].isna() & df["Credit"].isna() & df["Description"].str.contains(_NON_TXN_RE, na=False)
    df = df[~balance_only]
    df = df.drop_duplicates(subset=["Date", "Description", "Reference", "Debit", "Credit", "Balance"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ("Debit", "Credit", "Amount", "Balance"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    return df.reset_index(drop=True)


# ------------------------------------------------------------- math validation
@dataclass
class ValidationReport:
    n_rows: int
    total_debit: float
    total_credit: float
    n_checked: int
    n_mismatch: int
    direction: str  # "chronological" | "reverse" | "n/a"
    n_autofixed: int = 0

    @property
    def ok(self) -> bool:
        return self.n_mismatch == 0

    @property
    def net(self) -> float:
        return self.total_credit - self.total_debit


def _check_series(frame: pd.DataFrame, tol: float = 0.011) -> pd.Series:
    bal = frame["Balance"]
    prev = bal.shift(1)
    expected = prev + frame["Amount"].fillna(0)
    ok = (bal - expected).abs() <= tol
    applicable = bal.notna() & prev.notna()
    return ok.where(applicable, other=pd.NA)


def validate_running_balance(df: pd.DataFrame, tol: float = 0.011, autofix: bool = True) -> tuple[pd.DataFrame, ValidationReport]:
    """Check ``balance[i] == balance[i-1] + credit[i] - debit[i]``.

    Statements may be printed oldest-first or newest-first; both directions
    are tried and the one with more consistent rows wins.  With ``autofix``
    a row whose amount landed in the wrong column (debit vs credit) is
    swapped when, and only when, the swap reconciles the printed balance.
    Adds a boolean ``Balance OK`` column (``None`` where not applicable).
    """
    df = df.copy()
    total_debit = float(df["Debit"].fillna(0).sum()) if not df.empty else 0.0
    total_credit = float(df["Credit"].fillna(0).sum()) if not df.empty else 0.0

    if df.empty or df["Balance"].notna().sum() < 2:
        df["Balance OK"] = None
        return df, ValidationReport(len(df), total_debit, total_credit, 0, 0, "n/a")

    order = df.index.tolist()
    n_autofixed = 0
    if autofix:
        # Decide direction on the raw data, then walk it once, swapping
        # Debit<->Credit where (and only where) the swap reconciles the
        # printed balance exactly.  A wrong-column amount is the most common
        # small-model slip and the printed balance is hard evidence.
        fwd_hits = int((_check_series(df) == True).sum())  # noqa: E712
        rev_hits = int((_check_series(df.iloc[::-1]) == True).sum())  # noqa: E712
        if rev_hits > fwd_hits:
            order = order[::-1]
        prev_bal = None
        for idx in order:
            bal = df.at[idx, "Balance"]
            if prev_bal is not None and pd.notna(bal):
                amt = df.at[idx, "Amount"]
                amt = 0.0 if pd.isna(amt) else amt
                if abs(bal - (prev_bal + amt)) > tol and abs(bal - (prev_bal - amt)) <= tol and amt != 0:
                    df.at[idx, "Debit"], df.at[idx, "Credit"] = df.at[idx, "Credit"], df.at[idx, "Debit"]
                    df.at[idx, "Amount"] = -amt
                    n_autofixed += 1
            if pd.notna(bal):
                prev_bal = bal
        total_debit = float(df["Debit"].fillna(0).sum())
        total_credit = float(df["Credit"].fillna(0).sum())

    fwd = _check_series(df, tol)
    rev = _check_series(df.iloc[::-1], tol).iloc[::-1]
    fwd_hits, rev_hits = int((fwd == True).sum()), int((rev == True).sum())  # noqa: E712
    if rev_hits > fwd_hits:
        chosen, direction = rev, "reverse"
    else:
        chosen, direction = fwd, "chronological"

    df["Balance OK"] = chosen.astype("object").where(chosen.notna(), None)
    n_checked = int(chosen.notna().sum())
    n_mismatch = int((chosen == False).sum())  # noqa: E712
    return df, ValidationReport(len(df), total_debit, total_credit, n_checked, n_mismatch, direction, n_autofixed)
