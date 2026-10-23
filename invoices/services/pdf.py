"""Render an invoice as an A4 PDF with fpdf2 (pure Python, no system deps)."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from fpdf import FPDF
from fpdf.enums import TableCellFillMode
from fpdf.fonts import FontFace

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
FONT = "DejaVu"
INK = (33, 37, 41)
MUTED = (108, 117, 125)
ACCENT = (37, 99, 235)
RULE = (222, 226, 230)


def _date(value) -> str:
    return value.strftime("%d %b %Y") if value else ""


class InvoicePDF(FPDF):
    def __init__(self, invoice):
        super().__init__(orientation="portrait", unit="mm", format="A4")
        self.invoice = invoice
        self.add_font(FONT, "", str(FONT_DIR / "DejaVuSans.ttf"))
        self.add_font(FONT, "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(18, 18, 18)
        self.set_title(f"Invoice {invoice.number}")
        self.set_author(settings.BUSINESS_NAME)
        self.set_creator("django-invoice-reminders")

    def footer(self):
        self.set_y(-14)
        self.set_font(FONT, "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"{self.invoice.number} · Page {self.page_no()}/{{nb}}", align="C")


def render_invoice_pdf(invoice) -> bytes:
    pdf = InvoicePDF(invoice)
    pdf.add_page()
    width = pdf.epw
    left = pdf.l_margin

    # Header: business on the left, INVOICE on the right.
    top = pdf.get_y()
    pdf.set_text_color(*INK)
    pdf.set_font(FONT, "B", 15)
    pdf.multi_cell(width * 0.6, 7, settings.BUSINESS_NAME, new_x="LMARGIN", new_y="NEXT")
    header_bottom = pdf.get_y()

    pdf.set_xy(left + width * 0.6, top)
    pdf.set_font(FONT, "B", 22)
    pdf.set_text_color(*ACCENT)
    pdf.cell(width * 0.4, 10, "INVOICE", align="R", new_x="LEFT", new_y="NEXT")
    pdf.set_font(FONT, "", 9.5)
    pdf.set_text_color(*INK)
    for label, value in (
        ("Invoice no.", invoice.number),
        ("Issue date", _date(invoice.issue_date)),
        ("Due date", _date(invoice.due_date)),
    ):
        pdf.set_x(left + width * 0.6)
        pdf.cell(width * 0.4, 5.5, f"{label}:  {value}", align="R", new_x="LEFT", new_y="NEXT")
    if invoice.status == invoice.Status.PAID:
        pdf.set_x(left + width * 0.6)
        pdf.set_font(FONT, "B", 10)
        pdf.set_text_color(22, 163, 74)
        pdf.cell(width * 0.4, 6, "PAID", align="R", new_x="LEFT", new_y="NEXT")

    pdf.set_y(max(header_bottom, pdf.get_y()) + 6)
    pdf.set_draw_color(*RULE)
    pdf.line(left, pdf.get_y(), left + width, pdf.get_y())
    pdf.ln(5)

    # Bill to.
    client = invoice.client
    pdf.set_font(FONT, "B", 8.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, "BILL TO", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT, "B", 11)
    pdf.set_text_color(*INK)
    pdf.multi_cell(width, 5.5, client.name, new_x="LMARGIN", new_y="NEXT")
    if client.company:
        pdf.set_font(FONT, "", 9.5)
        pdf.multi_cell(width, 5, client.company, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # Line items.
    pdf.set_font(FONT, "", 9.5)
    pdf.set_draw_color(*RULE)
    with pdf.table(
        col_widths=(52, 12, 18, 18),
        text_align=("LEFT", "RIGHT", "RIGHT", "RIGHT"),
        line_height=6.5,
        padding=(1.5, 2),
        headings_style=FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=ACCENT),
        cell_fill_color=(248, 249, 250),
        cell_fill_mode=TableCellFillMode.ROWS,
        borders_layout="HORIZONTAL_LINES",
    ) as table:
        table.row(["Description", "Qty", "Unit price", "Amount"])
        for item in invoice.line_items.all():
            table.row(
                [
                    item.description,
                    f"{item.quantity.normalize():,f}",
                    f"{item.unit_price:,.2f}",
                    f"{item.amount:,.2f}",
                ]
            )

    # Totals.
    pdf.ln(4)
    rows = [("Subtotal", invoice.format_money(invoice.subtotal))]
    if invoice.tax_rate:
        rows.append((f"Tax ({invoice.tax_rate.normalize():f}%)", invoice.format_money(invoice.tax_amount)))
    for label, value in rows:
        pdf.set_x(left + width * 0.5)
        pdf.set_font(FONT, "", 9.5)
        pdf.cell(width * 0.25, 6, label, align="R")
        pdf.cell(width * 0.25, 6, value, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(left + width * 0.5)
    pdf.line(left + width * 0.5, pdf.get_y() + 1, left + width, pdf.get_y() + 1)
    pdf.ln(2)
    pdf.set_x(left + width * 0.5)
    pdf.set_font(FONT, "B", 11.5)
    pdf.cell(width * 0.25, 8, "Total due", align="R")
    pdf.cell(width * 0.25, 8, invoice.total_display, align="R", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())

