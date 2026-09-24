import pytest

from converter.llm_parser import OllamaClient, OllamaError, chunk_pages, extract_json, parse_statement


def test_extract_json_plain():
    assert extract_json('{"transactions": []}') == {"transactions": []}


def test_extract_json_code_fence_and_chatter():
    txt = 'Sure! Here you go:\n```json\n{"transactions": [{"date": "1/1/25"}]}\n```\nHope this helps.'
    assert extract_json(txt)["transactions"][0]["date"] == "1/1/25"


def test_extract_json_bare_list_is_wrapped():
    assert extract_json("[{\"date\": \"x\"}]") == {"transactions": [{"date": "x"}]}


def test_extract_json_failure():
    with pytest.raises(OllamaError):
        extract_json("no json here")


def test_chunk_pages_groups_small_pages():
    pages = ["a" * 100, "b" * 100, "c" * 100]
    chunks = chunk_pages(pages, max_chars=250)
    assert [(f, l) for f, l, _ in chunks] == [(1, 2), (3, 3)]


def test_chunk_pages_splits_oversized_page():
    big = "\n".join(f"line {i}" for i in range(200))
    chunks = chunk_pages(["small", big, "tail"], max_chars=400)
    assert chunks[0][:2] == (1, 1)
    assert all(f == l == 2 for f, l, _ in chunks[1:-1])
    assert chunks[-1][:2] == (3, 3)
    assert all(len(t) <= 400 + 20 for _, _, t in chunks)


def test_chunk_pages_skips_blank():
    assert chunk_pages(["", "   "]) == []


class _FakeClient(OllamaClient):
    def __init__(self):
        super().__init__(host="http://fake")
        self.calls = []

    def chat_json(self, model, system, user, schema=None, num_ctx=8192, temperature=0.0):
        self.calls.append(user)
        return {"transactions": [{"date": "1/1/25", "description": f"call {len(self.calls)}", "reference": "", "debit": "1", "credit": "", "balance": ""}]}


def test_parse_statement_concatenates_chunks_and_tags_page():
    client = _FakeClient()
    seen = []
    rows = parse_statement(["p1" * 10, "p2" * 10], client, model="m", max_chars=15, progress=lambda i, n: seen.append((i, n)))
    assert len(rows) == 2
    assert [r["_page"] for r in rows] == [1, 2]
    assert seen == [(1, 2), (2, 2)]
    assert "<statement>" in client.calls[0]


def test_client_unreachable_raises():
    client = OllamaClient(host="http://127.0.0.1:9", timeout=2)
    assert not client.is_available()
    assert client.list_models() == []
    with pytest.raises(OllamaError):
        client.chat_json("m", "s", "u")
