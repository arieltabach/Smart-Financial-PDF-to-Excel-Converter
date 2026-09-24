"""A tiny stand-in for the Ollama HTTP API, for offline UI/e2e testing.

It answers /api/tags and /api/chat and returns a canned set of transactions
(the ones from samples/demo_statement.pdf) regardless of the prompt.

Usage:  python scripts/fake_ollama.py [port]      # default 11435
        OLLAMA_HOST=http://localhost:11435 streamlit run app.py
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

CANNED = {
    "transactions": [
        {"date": "02/02/2025", "description": "SUPER-PHARM TEL AVIV", "reference": "4471", "debit": "156.90", "credit": "", "balance": "2,293.10"},
        {"date": "03/02/2025", "description": "Salary - ACME LTD", "reference": "TRF 88213", "debit": "", "credit": "12,500.00", "balance": "14,793.10"},
        {"date": "05/02/2025", "description": "Electric Corp bill", "reference": "BILL 0192", "debit": "412.35", "credit": "", "balance": "14,380.75"},
        {"date": "07/02/2025", "description": "ATM withdrawal", "reference": "ATM 2201", "debit": "800.00", "credit": "", "balance": "13,580.75"},
        {"date": "10/02/2025", "description": "Amazon.com refund", "reference": "RF 5510", "debit": "", "credit": "89.99", "balance": "13,670.74"},
        {"date": "12/02/2025", "description": "Rent - February", "reference": "STO 7", "debit": "4,300.00", "credit": "", "balance": "9,370.74"},
        {"date": "15/02/2025", "description": "Wolt delivery", "reference": "4471", "debit": "74.50", "credit": "", "balance": "9,296.24"},
        {"date": "20/02/2025", "description": "Interest credit", "reference": "", "debit": "", "credit": "3.12", "balance": "9,299.36"},
        {"date": "28/02/2025", "description": "Cellcom monthly plan", "reference": "DD 331", "debit": "99.90", "credit": "", "balance": "9,199.46"},
    ]
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/tags"):
            self._send({"models": [{"name": "fake-model:latest"}]})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path.startswith("/api/chat"):
            self._send({"message": {"role": "assistant", "content": json.dumps(CANNED, ensure_ascii=False)}, "done": True})
        else:
            self.send_error(404)

    def log_message(self, *_):  # quiet
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11435
    print(f"fake ollama listening on http://localhost:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
