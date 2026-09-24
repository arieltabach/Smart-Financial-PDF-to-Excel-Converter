"""Smart Financial PDF → Excel converter.

Pipeline:  PDF  →  text extraction (pdfplumber)  →  local LLM parsing (Ollama)
           →  cleaning + math validation (pandas)  →  Excel export (openpyxl)
"""

__version__ = "0.1.0"
