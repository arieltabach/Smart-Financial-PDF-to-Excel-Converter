"""Parse raw statement text into structured transactions with a *free* local
LLM served by Ollama (https://ollama.com).  No paid API is ever called.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable

import requests

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1:8b"

# Ollama structured-output schema.  Everything is a string on purpose: small
# models are far more reliable at *copying* "1,234.56" than at emitting a
# valid float, and the pandas cleaning step normalises the numbers anyway.
TRANSACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "description": {"type": "string"},
                    "reference": {"type": "string"},
                    "debit": {"type": "string"},
                    "credit": {"type": "string"},
                    "balance": {"type": "string"},
                },
                "required": ["date", "description", "reference", "debit", "credit", "balance"],
            },
        }
    },
    "required": ["transactions"],
}

SYSTEM_PROMPT = """You are a meticulous financial data-entry clerk.
You receive raw text extracted from a bank or credit-card statement (it may be
in English, Hebrew or mixed, and column alignment may be imperfect).
Extract EVERY transaction row into JSON matching the schema exactly.

Rules:
- One JSON object per transaction line. Do not merge or invent rows.
- Skip page headers, footers, column titles, opening/closing balance lines,
  subtotals, totals, marketing text and account metadata.
- date: copy the date exactly as printed (e.g. "03/02/2025" or "3.2.25").
  If a row has both a transaction date and a value/posting date, use the
  transaction date.
- description: the merchant / payee / transaction text, copied verbatim.
  Keep Hebrew text as Hebrew; do not translate or transliterate.
- reference: reference number, voucher, card last-4 digits or any extra
  detail column. Empty string if none.
- debit: money leaving the account (charges, withdrawals, payments). Copy the
  number as printed, including separators. Empty string if none.
- credit: money entering the account (deposits, refunds, income). Empty string
  if none.
- If the statement has a single signed amount column, put negative amounts in
  debit (without the sign) and positive amounts in credit.
- balance: running balance after the transaction if printed, else empty.
- Never output null; use "" for missing values.
Return ONLY the JSON object."""

USER_PROMPT_TEMPLATE = """Statement text (pages {first}-{last} of {total}):

<statement>
{text}
</statement>

Return the JSON now."""


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an unusable response."""


@dataclass
class OllamaClient:
    host: str = DEFAULT_HOST
    timeout: int = 600  # local 7-8B models can take a while on CPU

    # ------------------------------------------------------------------ status
    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=3)
            return r.ok
        except requests.RequestException:
            return False

    def list_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        except (requests.RequestException, ValueError, KeyError):
            return []

    # -------------------------------------------------------------------- chat
    def chat_json(
        self,
        model: str,
        system: str,
        user: str,
        schema: dict | None = None,
        num_ctx: int = 8192,
        temperature: float = 0.0,
    ) -> dict:
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": schema if schema is not None else "json",
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }
        try:
            r = requests.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise OllamaError(
                f"Could not reach Ollama at {self.host}. Is `ollama serve` running? ({exc})"
            ) from exc
        if r.status_code == 404:
            raise OllamaError(
                f"Model '{model}' not found. Pull it first:  ollama pull {model}"
            )
        if not r.ok:
            raise OllamaError(f"Ollama returned HTTP {r.status_code}: {r.text[:300]}")
        try:
            content = r.json()["message"]["content"]
        except (ValueError, KeyError) as exc:
            raise OllamaError(f"Unexpected Ollama response: {r.text[:300]}") from exc
        return extract_json(content)


# ------------------------------------------------------------------ utilities
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> dict:
    """Best-effort JSON extraction from a model reply.

    Handles code fences, leading chatter and trailing commentary.  Raises
    ``OllamaError`` if no JSON object can be recovered.
    """
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else {"transactions": obj}
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise OllamaError(f"Model did not return valid JSON:\n{text[:500]}")


def chunk_pages(pages: list[str], max_chars: int = 6000) -> list[tuple[int, int, str]]:
    """Group consecutive pages into chunks of at most ``max_chars``.

    Returns ``(first_page, last_page, text)`` tuples (1-based, inclusive).
    A single oversized page is split on line boundaries.
    """
    chunks: list[tuple[int, int, str]] = []
    buf: list[str] = []
    buf_len = 0
    first = 1

    def flush(last: int) -> None:
        nonlocal buf, buf_len, first
        if buf:
            chunks.append((first, last, "\n".join(buf)))
        buf, buf_len, first = [], 0, last + 1

    for idx, page in enumerate(pages, start=1):
        if len(page) > max_chars:
            flush(idx - 1)
            for piece in _split_long(page, max_chars):
                chunks.append((idx, idx, piece))
            first = idx + 1
            continue
        if buf and buf_len + len(page) > max_chars:
            flush(idx - 1)
        buf.append(page)
        buf_len += len(page)
    flush(len(pages))
    return [c for c in chunks if c[2].strip()]


def _split_long(text: str, max_chars: int) -> Iterable[str]:
    piece: list[str] = []
    size = 0
    for line in text.splitlines():
        if piece and size + len(line) > max_chars:
            yield "\n".join(piece)
            piece, size = [], 0
        piece.append(line)
        size += len(line) + 1
    if piece:
        yield "\n".join(piece)


def parse_statement(
    pages: list[str],
    client: OllamaClient,
    model: str = DEFAULT_MODEL,
    max_chars: int = 6000,
    num_ctx: int = 8192,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """Run the LLM over every chunk and return the concatenated raw rows."""
    chunks = chunk_pages(pages, max_chars=max_chars)
    total = len(chunks)
    rows: list[dict] = []
    for i, (first, last, text) in enumerate(chunks, start=1):
        user = USER_PROMPT_TEMPLATE.format(first=first, last=last, total=len(pages), text=text)
        result = client.chat_json(model, SYSTEM_PROMPT, user, TRANSACTION_SCHEMA, num_ctx=num_ctx)
        for row in result.get("transactions", []) or []:
            if isinstance(row, dict):
                row["_page"] = first
                rows.append(row)
        if progress:
            progress(i, total)
    return rows
