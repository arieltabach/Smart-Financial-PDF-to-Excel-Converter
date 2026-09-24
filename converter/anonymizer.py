"""Privacy layer: mask identifiers *before* text is sent to a (possibly remote)
LLM and restore the originals in the parsed result afterwards.

Golden rule: **replace, never remove**.  Every sensitive value is swapped for a
synthetic value of the same shape (same length, same separators, same
character class), so the model still sees a realistic statement and its
column-mapping behaviour does not change.  The mapping never leaves the
process; the model only ever sees the fake values.

What is masked
--------------
* long digit runs (>= 6 digits) that are **not** amounts or dates: account
  numbers, card numbers, Israeli IDs, phone numbers, transaction ids
* hyphen/space-grouped account & card formats (``12-345-678901``,
  ``4580 1234 5678 9012``, ``**** 1234``)
* IBANs and SWIFT/BIC codes
* e-mail addresses
* the text after account-holder / address labels (EN + HE)
* any extra words the user supplies (their own name, employer, street ...)

What is deliberately **not** masked: dates, amounts, running balances, short
references (< 6 digits) and merchant names — the parser needs them, and they
do not identify the account holder on their own.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import string
from collections import Counter
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- patterns
# An amount: 1,234.56 / 1234.56 / 12,500 — digit groups with , or . separators.
_AMOUNT_RE = re.compile(r"(?<![\w.,-])-?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?(?![\w.,])|(?<![\w.,-])-?\d+\.\d{1,2}(?![\w.,])")
# A date: 3/2/25, 03.02.2025, 2025-02-03
_DATE_RE = re.compile(r"(?<!\d)(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})(?!\d)")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){3,7}(?:[ -]?[A-Z0-9]{1,4})?\b")
_SWIFT_RE = re.compile(r"\b(?:SWIFT|BIC)\s*[:.]?\s*([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b", re.IGNORECASE)
# Grouped identifiers: 12-345-678901, 4580 1234 5678 9012, 054-1234567
_GROUPED_RE = re.compile(r"(?<![\d.,])\d{2,4}(?:[ -]\d{2,7}){1,4}(?![\d.,])")
# Partially masked card: **** 1234 / ****-****-****-1234 / XXXX1234
_MASKED_CARD_RE = re.compile(r"(?<![\w])(?:[*xX]{2,}[ -]?){1,3}\d{4}(?![\d.,])")
# Plain long digit runs (>= 6) not part of an amount/date (those were protected earlier).
_LONG_DIGITS_RE = re.compile(r"(?<![\d.,])\d{6,}(?![\d.,])")

# Labels whose following text is the customer's identity, not a transaction.
_LABEL_RE = re.compile(
    r"(?im)^(?P<label>[^\n]{0,40}?\b(?:account holder|account name|customer(?: name)?|name|dear|address|"
    r"לכבוד|שם(?: הלקוח| בעל החשבון)?|בעל(?:י)? החשבון|כתובת|רח'|רחוב|מען)\s*[:\-]?\s*)(?P<value>[^\n|]{2,80})$"
)

_PLACEHOLDER = "\uE000{}\uE001"  # private-use sentinels protect amounts/dates during masking


@dataclass
class MaskReport:
    counts: Counter = field(default_factory=Counter)
    n_values: int = 0

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> str:
        if not self.counts:
            return "nothing to mask"
        parts = [f"{n} {k}" for k, n in sorted(self.counts.items(), key=lambda kv: -kv[1])]
        return ", ".join(parts)


class Anonymizer:
    """Deterministic, reversible, format-preserving masker.

    One instance = one document.  ``mask()`` may be called on several pages;
    the same original always maps to the same fake within the instance.
    """

    def __init__(self, extra_terms: list[str] | None = None, salt: str | None = None):
        self._salt = salt or secrets.token_hex(8)
        self._fwd: dict[str, str] = {}   # original -> fake
        self._rev: dict[str, str] = {}   # fake (and its normalised forms) -> original
        self._used: set[str] = set()
        self._name_counter = 0
        self.extra_terms = [t.strip() for t in (extra_terms or []) if t and t.strip()]
        self.report = MaskReport()

    # ------------------------------------------------------------- public API
    def mask(self, text: str) -> str:
        if not text:
            return text
        protected: list[str] = []

        def _shield(value: str) -> str:
            protected.append(value)
            return _PLACEHOLDER.format(len(protected) - 1)

        def _protect(m: re.Match) -> str:
            return _shield(m.group(0))

        def _sub(category: str, maker):
            # Every fake is shielded immediately so no later rule can re-mask it.
            return lambda m: _shield(self._map(m.group(0), category, maker))

        # 1. shield amounts and dates so no rule below can touch them
        work = _DATE_RE.sub(_protect, text)
        work = _AMOUNT_RE.sub(_protect, work)

        # 2. identity lines (labels) and user-supplied terms
        work = _LABEL_RE.sub(lambda m: self._mask_label_line(m, _shield), work)
        for term in sorted(self.extra_terms, key=len, reverse=True):
            work = re.sub(re.escape(term), _sub("names", self._fake_name), work, flags=re.IGNORECASE)

        # 3. structured identifiers, most specific first
        work = _EMAIL_RE.sub(_sub("emails", self._fake_email), work)
        work = _IBAN_RE.sub(_sub("ibans", self._fake_same_shape), work)
        work = _SWIFT_RE.sub(
            lambda m: m.group(0).replace(m.group(1), _shield(self._map(m.group(1), "swift codes", self._fake_same_shape))), work
        )
        work = _MASKED_CARD_RE.sub(_sub("account/card numbers", self._fake_same_shape), work)
        work = _GROUPED_RE.sub(_sub("account/card numbers", self._fake_same_shape), work)
        work = _LONG_DIGITS_RE.sub(_sub("id/reference numbers", self._fake_same_shape), work)

        # 4. un-shield (placeholders never nest, one pass is enough)
        return re.sub("\uE000(\\d+)\uE001", lambda m: protected[int(m.group(1))], work)

    def restore(self, text: str) -> str:
        """Put the originals back into a string produced by the model."""
        if not text or not self._rev:
            return text
        # longest fakes first so a short fake never clobbers part of a long one
        for fake in sorted(self._rev, key=len, reverse=True):
            if fake in text:
                text = text.replace(fake, self._rev[fake])
        return text

    def masked_preview(self, text: str, limit: int = 4000) -> str:
        """What the model will see, for display in the UI."""
        return self.mask(text)[:limit]

    def restore_rows(self, rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            out.append({k: (self.restore(v) if isinstance(v, str) else v) for k, v in row.items()})
        return out

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._fwd)

    # ---------------------------------------------------------------- helpers
    def _map(self, original: str, category: str, maker) -> str:
        if original in self._fwd:
            return self._fwd[original]
        fake = maker(original)
        bump = 0
        while fake == original or fake in self._used:
            bump += 1
            fake = maker(original + f"#{bump}") if maker is not self._fake_name else self._fake_name(original + f"#{bump}")
        self._fwd[original] = fake
        self._used.add(fake)
        self._rev[fake] = original
        # the model may drop separators or spaces from a fake: map those forms too
        for variant in {re.sub(r"[ -]", "", fake), fake.replace(" ", ""), fake.replace("-", "")}:
            if variant != fake and variant not in self._rev:
                self._rev[variant] = re.sub(r"[ -]", "", original) if variant == re.sub(r"[ -]", "", fake) else original
        self.report.counts[category] += 1
        self.report.n_values = len(self._fwd)
        return fake

    def _digest(self, seed: str) -> str:
        return hashlib.sha256(f"{self._salt}|{seed}".encode()).hexdigest()

    def _fake_same_shape(self, original: str) -> str:
        """Same length, same separators, digits stay digits, letters stay letters."""
        base = original.split("#")[0]
        h = self._digest(original)
        out, i = [], 0
        for ch in base:
            if ch.isdigit():
                out.append(str(int(h[i % len(h)], 16) % 10)); i += 1
            elif ch.isalpha() and ch.isupper():
                out.append(string.ascii_uppercase[int(h[i % len(h)], 16) % 26]); i += 1
            elif ch.isalpha():
                out.append(string.ascii_lowercase[int(h[i % len(h)], 16) % 26]); i += 1
            else:
                out.append(ch)
        return "".join(out)

    def _fake_email(self, original: str) -> str:
        return f"user{int(self._digest(original)[:6], 16) % 10000:04d}@masked.example"

    def _fake_name(self, original: str) -> str:
        self._name_counter += 1
        base = original.split("#")[0]
        return f"Customer {chr(64 + ((self._name_counter - 1) % 26) + 1)}" if not _has_hebrew(base) else f"לקוח {self._name_counter}"

    def _mask_label_line(self, m: re.Match, shield) -> str:
        label, value = m.group("label"), m.group("value")
        if "\uE000" in value:  # value is a protected amount/date, e.g. "Value date: 03/02/2025"
            return m.group(0)
        if _looks_like_column_header(value):
            return m.group(0)
        fake = self._map(value.strip(), "identity lines", self._fake_name)
        return label + shield(fake) + value[len(value.rstrip()):]


def _has_hebrew(s: str) -> bool:
    return bool(re.search(r"[֐-׿]", s))


def _looks_like_column_header(value: str) -> bool:
    # "Description | Reference | Debit" style lines must not be treated as identities
    return "|" in value or bool(re.fullmatch(r"[\w\s/()]{0,30}(?:Debit|Credit|Balance|חובה|זכות|יתרה)[\w\s/()|]*", value, flags=re.IGNORECASE))
