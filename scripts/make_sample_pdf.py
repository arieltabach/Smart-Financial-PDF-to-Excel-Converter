"""Generate a small synthetic bank statement PDF for testing.

Usage:  python scripts/make_sample_pdf.py [output.pdf]
The data is invented; amounts are chosen so the running balance is consistent.
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet

ROWS = [
    # date, description, reference, debit, credit
    ("01/02/2025", "Opening balance", "", "", ""),
    ("02/02/2025", "SUPER-PHARM TEL AVIV", "4471", "156.90", ""),
    ("03/02/2025", "Salary - ACME LTD", "TRF 88213", "", "12,500.00"),
    ("05/02/2025", "Electric Corp bill", "BILL 0192", "412.35", ""),
    ("07/02/2025", "ATM withdrawal", "ATM 2201", "800.00", ""),
    ("10/02/2025", "Amazon.com refund", "RF 5510", "", "89.99"),
    ("12/02/2025", "Rent - February", "STO 7", "4,300.00", ""),
    ("15/02/2025", "Wolt delivery", "4471", "74.50", ""),
    ("20/02/2025", "Interest credit", "", "", "3.12"),
    ("28/02/2025", "Cellcom monthly plan", "DD 331", "99.90", ""),
]
OPENING = 2_450.00


def build_rows():
    bal = OPENING
    out = [("Date", "Description", "Reference", "Debit", "Credit", "Balance")]
    for date, desc, ref, debit, credit in ROWS:
        d = float(debit.replace(",", "")) if debit else 0.0
        c = float(credit.replace(",", "")) if credit else 0.0
        bal = bal + c - d
        out.append((date, desc, ref, debit, credit, f"{bal:,.2f}"))
    return out


def main(path: str) -> None:
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("<b>Demo Bank Ltd.</b> — Current Account Statement", styles["Title"]),
        Paragraph("Account 12-345-678901 · Statement period 01/02/2025 – 28/02/2025 · Currency ILS", styles["Normal"]),
        Spacer(1, 8 * mm),
    ]
    table = Table(build_rows(), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F6F8B")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story += [table, Spacer(1, 6 * mm), Paragraph("This statement is generated for testing purposes only.", styles["Italic"])]
    doc.build(story)
    print(f"wrote {path}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / "samples" / "demo_statement.pdf")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    main(out)
