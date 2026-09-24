"""Smart Financial PDF → Excel Converter (Streamlit UI).

Run:  streamlit run app.py
Everything runs locally: pdfplumber for extraction, a free open-source model
served by Ollama for parsing, pandas for cleaning, openpyxl for Excel.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from converter.data_cleaner import COLUMNS, rows_to_dataframe, validate_running_balance
from converter.excel_exporter import to_excel_bytes
from converter.llm_parser import DEFAULT_HOST, DEFAULT_MODEL, OllamaClient, OllamaError, parse_statement
from converter.pdf_extractor import contains_hebrew, extract_pdf

st.set_page_config(page_title="PDF → Excel Statement Converter", page_icon="📊", layout="wide")

# ----------------------------------------------------------------- session
for key, default in {
    "df": None,
    "report": None,
    "extracted": None,
    "source_name": "",
    "elapsed": 0.0,
}.items():
    st.session_state.setdefault(key, default)


# ----------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("⚙️ Settings")
    host = st.text_input("Ollama host", value=os.getenv("OLLAMA_HOST", DEFAULT_HOST), help="Where `ollama serve` is listening.")
    client = OllamaClient(host=host.rstrip("/"))

    available = client.is_available()
    models = client.list_models() if available else []
    if available:
        st.success(f"Ollama online · {len(models)} model(s)", icon="✅")
    else:
        st.error("Ollama offline", icon="❌")
        st.caption("Start it with `ollama serve` — see the setup guide below.")

    if models:
        default_idx = models.index(DEFAULT_MODEL) if DEFAULT_MODEL in models else 0
        model = st.selectbox("Model", models, index=default_idx)
    else:
        model = st.text_input("Model", value=DEFAULT_MODEL, help="Pull it with `ollama pull <model>`.")

    rtl_mode = st.radio(
        "Hebrew / RTL text fix",
        options=["auto", "on", "off"],
        horizontal=True,
        help="Many Hebrew PDFs extract with reversed letters. 'auto' detects and repairs this.",
    )
    dayfirst = st.toggle("Dates are day-first (DD/MM/YYYY)", value=True)

    with st.expander("Advanced"):
        max_chars = st.slider("Chunk size (characters per LLM call)", 2000, 16000, 6000, step=500,
                              help="Smaller chunks are slower but more reliable on small models.")
        num_ctx = st.select_slider("Model context window (num_ctx)", options=[4096, 8192, 16384, 32768], value=8192)
        show_raw = st.checkbox("Show extracted raw text", value=False)

    st.divider()
    st.markdown("**Setup guide:** [docs/OLLAMA_SETUP.md](https://github.com/arieltabach/Smart-Financial-PDF-to-Excel-Converter/blob/main/docs/OLLAMA_SETUP.md)")
    st.caption("100% local · zero API cost · your statements never leave this machine.")


# ----------------------------------------------------------------- main
st.title("📊 Smart Financial PDF → Excel Converter")
st.caption("Upload a bank or credit-card statement PDF → a free local LLM parses it → download a clean Excel file.")

uploaded = st.file_uploader("Drag & drop a statement PDF", type=["pdf"], accept_multiple_files=False)

col_btn, col_info = st.columns([1, 3])
convert = col_btn.button("🚀 Convert to Excel", type="primary", disabled=uploaded is None or not available, use_container_width=True)
if uploaded is None:
    col_info.info("Upload a PDF to get started.")
elif not available:
    col_info.warning("Ollama is not reachable. Start it and refresh the page.")


def run_pipeline(file_bytes: bytes, name: str) -> None:
    t0 = time.perf_counter()
    status = st.status("Converting…", expanded=True)

    status.write("📄 Extracting text from PDF…")
    doc = extract_pdf(file_bytes, rtl_mode=rtl_mode)
    st.session_state.extracted = doc
    status.write(
        f"Found {doc.n_pages} page(s), {doc.n_tables} table(s)"
        + (" · Hebrew detected" if doc.has_hebrew else "")
        + (" · RTL order repaired" if doc.rtl_fixed else "")
    )
    if not any(p.strip() for p in doc.pages):
        status.update(label="No text found", state="error")
        st.error("This PDF has no extractable text (probably a scanned image). OCR is not part of this MVP.")
        return

    status.write(f"🧠 Parsing with `{model}` via Ollama…")
    bar = status.progress(0.0)

    def _progress(i: int, n: int) -> None:
        bar.progress(i / n, text=f"Chunk {i}/{n}")

    try:
        rows = parse_statement(doc.pages, client, model=model, max_chars=max_chars, num_ctx=num_ctx, progress=_progress)
    except OllamaError as exc:
        status.update(label="LLM parsing failed", state="error")
        st.error(str(exc))
        return

    status.write(f"🧹 Cleaning {len(rows)} raw row(s) and validating balances…")
    df = rows_to_dataframe(rows, dayfirst=dayfirst)
    df, report = validate_running_balance(df)

    st.session_state.df = df
    st.session_state.report = report
    st.session_state.source_name = name
    st.session_state.elapsed = time.perf_counter() - t0
    status.update(label=f"Done in {st.session_state.elapsed:.1f}s", state="complete", expanded=False)


if convert and uploaded is not None:
    run_pipeline(uploaded.getvalue(), uploaded.name)


# ----------------------------------------------------------------- results
df: pd.DataFrame | None = st.session_state.df
report = st.session_state.report
doc = st.session_state.extracted

if show_raw and doc is not None:
    with st.expander("Extracted raw text", expanded=False):
        st.text(doc.text[:20000])

if df is not None:
    if df.empty:
        st.warning("The model did not return any transactions. Try a smaller chunk size, another model, or check the raw text.")
    else:
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Transactions", report.n_rows)
        m2.metric("Total debit", f"{report.total_debit:,.2f}")
        m3.metric("Total credit", f"{report.total_credit:,.2f}")
        m4.metric("Net", f"{report.net:,.2f}")
        if report.n_checked:
            m5.metric("Balance check", "PASS" if report.ok else f"{report.n_mismatch} mismatch", delta=f"{report.n_checked} rows checked", delta_color="off")
        else:
            m5.metric("Balance check", "n/a", delta="no running balance", delta_color="off")

        if report.n_autofixed:
            st.info(f"{report.n_autofixed} row(s) had the amount in the wrong column (debit/credit) and were corrected using the printed running balance.", icon="🔧")
        if report.n_checked and not report.ok:
            st.warning(
                f"{report.n_mismatch} row(s) fail the running-balance check "
                f"(balance ≠ previous balance + credit − debit). They are highlighted in the Excel. "
                "Edit the table below to fix them before downloading."
            )

        st.subheader("Preview")
        st.caption("The table is editable — fix any parsing slips here and the Excel will reflect your edits.")
        show_cols = COLUMNS + (["Balance OK"] if "Balance OK" in df.columns else [])
        edited = st.data_editor(
            df[show_cols],
            use_container_width=True,
            num_rows="dynamic",
            hide_index=True,
            column_config={
                "Date": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Debit": st.column_config.NumberColumn(format="%.2f"),
                "Credit": st.column_config.NumberColumn(format="%.2f"),
                "Amount": st.column_config.NumberColumn(format="%.2f", disabled=True),
                "Balance": st.column_config.NumberColumn(format="%.2f"),
                "Balance OK": st.column_config.CheckboxColumn(disabled=True),
            },
            key="editor",
        )

        # Recompute derived columns after manual edits.
        out = edited.copy()
        out["Amount"] = out["Credit"].fillna(0) - out["Debit"].fillna(0)
        out.loc[out["Credit"].isna() & out["Debit"].isna(), "Amount"] = None
        out, out_report = validate_running_balance(out)

        rtl = doc.has_hebrew if doc is not None else contains_hebrew(" ".join(out["Description"].astype(str)))
        excel_name = Path(st.session_state.source_name).stem + ".xlsx"
        st.download_button(
            "⬇️ Download Excel",
            data=to_excel_bytes(out, report=out_report, rtl=rtl, source_name=st.session_state.source_name),
            file_name=excel_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
