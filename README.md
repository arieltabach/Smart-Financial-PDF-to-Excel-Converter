# Smart Financial PDF → Excel Converter

Convert bank and credit-card statement PDFs into clean, structured Excel files —
**100 % free and local**. Text extraction, LLM parsing, validation and export all
run on your machine. No OpenAI / Anthropic keys, no per-page fees, and your
financial data never leaves your laptop.

```
 PDF ──▶ pdfplumber ──▶ Ollama (Llama 3 / Qwen / Mistral) ──▶ pandas ──▶ openpyxl ──▶ .xlsx
        text + tables      free local LLM → strict JSON       clean +          formatted,
        RTL/Hebrew fix                                        math-validate    RTL-aware
```

## Features

* **Drag-and-drop** Streamlit UI, one *Convert to Excel* button, editable preview, download button.
* **PDF extraction** with `pdfplumber` (layout-preserving text **and** ruled tables).
* **Hebrew / RTL repair** – detects visually-ordered Hebrew (reversed letters) and restores logical order with the Unicode bidi algorithm.
* **Free LLM parsing via Ollama** – structured-output JSON schema, zero temperature, page-aware chunking so long statements fit small context windows.
* **Cleaning** – ₪/$/€ symbols, thousands separators, `(negatives)`, trailing minus, bidi marks; day-first dates; signed-amount normalisation into Debit/Credit.
* **Math validation** – checks every running balance (`balance = previous + credit − debit`), auto-detects oldest-first vs newest-first statements, flags mismatches, and **auto-corrects** an amount the model put in the wrong column when the printed balance proves the swap.
* **Table-aware prompting** – ruled tables are sent to the model as `Header: value` cells, so it never has to count columns; opening/closing balance lines are filtered deterministically (English + Hebrew).
* **Excel export** – styled header, number/date formats, auto-fit widths, freeze panes, auto-filter, mismatch rows highlighted, `Summary` sheet, **sheet direction RTL** when Hebrew is present.

## Quick start

```bash
git clone https://github.com/arieltabach/Smart-Financial-PDF-to-Excel-Converter.git
cd Smart-Financial-PDF-to-Excel-Converter
python3.12 -m venv .venv && source .venv/bin/activate      # 3.10 – 3.13 supported
pip install -r requirements.txt
```

Install Ollama and pull a model (full guide: [docs/OLLAMA_SETUP.md](docs/OLLAMA_SETUP.md)):

```bash
brew install ollama            # macOS; see the guide for Linux/Windows
ollama serve                   # keep running (the desktop app does this for you)
ollama pull llama3.1:8b        # or qwen2.5:7b for Hebrew statements
```

Run the app:

```bash
streamlit run app.py
```

Open <http://localhost:8501>, drop a PDF, click **Convert to Excel**, review the
preview (it is editable), download the `.xlsx`.

## Project structure

```
app.py                     Streamlit UI and pipeline orchestration
converter/
  pdf_extractor.py         pdfplumber text + tables, Hebrew/RTL detection & repair
  llm_parser.py            Ollama client, JSON schema, prompts, chunking
  data_cleaner.py          pandas cleaning, amount/date parsing, running-balance validation
  excel_exporter.py        openpyxl workbook: Transactions + Summary sheets
scripts/
  make_sample_pdf.py       generates samples/demo_statement.pdf (reportlab)
  fake_ollama.py           offline stand-in for the Ollama API (UI testing)
tests/                     pytest suite (parsers, validation, PDF, Excel round-trip)
docs/OLLAMA_SETUP.md       install / model / troubleshooting guide
samples/demo_statement.pdf synthetic statement for a first run
```

## Output schema

| Column | Type | Notes |
|---|---|---|
| Date | date | day-first by default (toggle in sidebar) |
| Description | text | verbatim, Hebrew preserved |
| Reference | text | reference / voucher / card digits |
| Debit | float | money out (always positive) |
| Credit | float | money in (always positive) |
| Amount | float | `Credit − Debit` (signed) |
| Balance | float | running balance if printed |
| Balance OK | OK / MISMATCH | running-balance check result |

## Settings (sidebar)

* **Ollama host** – defaults to `http://localhost:11434` (or `$OLLAMA_HOST`).
* **Model** – any model you have pulled; `llama3.1:8b` recommended, `qwen2.5:7b` for Hebrew.
* **Hebrew / RTL text fix** – `auto` (default), `on`, `off`.
* **Advanced** – chunk size per LLM call, `num_ctx`, raw-text viewer.

## Development

```bash
pip install -r requirements-dev.txt
python scripts/make_sample_pdf.py        # regenerate the sample
pytest                                   # 52 tests, ~1 s, no Ollama needed
```

Try the UI without a model:

```bash
python scripts/fake_ollama.py 11435 &
OLLAMA_HOST=http://localhost:11435 streamlit run app.py
```

## Verified

`samples/demo_statement.pdf` through the real pipeline with `qwen2.5:7b` on an
Apple M4 Pro: 9/9 transactions, all columns correct, running-balance check PASS,
about 14 s end to end. Three consecutive runs gave identical results.

## Limitations (MVP)

* Scanned / image-only PDFs are not OCR'd — pre-process with `ocrmypdf`.
* Accuracy depends on the local model; always glance at the *Balance check*
  metric and the highlighted rows. A mismatch usually means a row was skipped
  or a digit misread — fix it in the editable preview.
* One PDF per conversion; multi-file batching is a natural next step.
* Password-protected PDFs must be unlocked first.

## License

MIT
