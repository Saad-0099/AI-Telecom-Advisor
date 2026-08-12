"""
Phase 7 — PDF rendering.

Purely presentational: every figure and sentence comes from
report_content.build(). This module decides how things look, never what
they say.

reportlab rather than weasyprint: pure Python, installs cleanly on Windows.
weasyprint needs GTK system libraries and is a genuine setup headache.

VISUAL SEPARATION OF NUMBER TYPES
---------------------------------
Projected figures carry a dagger and sit on a tinted row. Simulated content
gets a full-width warning banner and a distinct heading colour. AI-written
prose sits in a marked box. A footnote alone is not enough - a reader
skimming a table must be able to see which numbers are measured, which are
assumed, and which sentences were generated.

Run:  python run.py report
"""

from __future__ import annotations

import io
import sys
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

import report_content as RC

# --------------------------------------------------------------------------
INK = colors.HexColor("#2C3543")
MUTED = colors.HexColor("#6B7280")
RULE = colors.HexColor("#D6DBE3")
BLUE = colors.HexColor("#5B8FF9")
RED = colors.HexColor("#E8684A")
AMBER = colors.HexColor("#F6BD16")
TINT = colors.HexColor("#F4F6FA")
SIM_TINT = colors.HexColor("#FFF7E6")     # simulated sections are warm-tinted
SIM_INK = colors.HexColor("#8A6D1F")


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=22,
                                textColor=INK, spaceAfter=2, alignment=TA_LEFT),
        "subtitle": ParagraphStyle("st", parent=base["Normal"], fontSize=10,
                                   textColor=MUTED, spaceAfter=14),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=14,
                             textColor=INK, spaceBefore=16, spaceAfter=6),
        "h2sim": ParagraphStyle("h2s", parent=base["Heading2"], fontSize=14,
                                textColor=SIM_INK, spaceBefore=16,
                                spaceAfter=6),
        "body": ParagraphStyle("b", parent=base["Normal"], fontSize=9.8,
                               leading=14.5, textColor=INK, spaceAfter=8),
        "small": ParagraphStyle("s", parent=base["Normal"], fontSize=8,
                                leading=11.5, textColor=MUTED),
        "banner": ParagraphStyle("bn", parent=base["Normal"], fontSize=8.8,
                                 leading=12.5, textColor=SIM_INK),
        "bullet": ParagraphStyle("bu", parent=base["Normal"], fontSize=9.3,
                                 leading=13.5, textColor=INK,
                                 leftIndent=10, spaceAfter=6),
    }


# ==========================================================================
def _tiles(items: list[tuple[str, str]], sim: bool = False) -> Table:
    per_row = 3
    rows = []
    for i in range(0, len(items), per_row):
        chunk = items[i:i + per_row]
        rows.append([c[1] for c in chunk] + [""] * (per_row - len(chunk)))
        rows.append([c[0] for c in chunk] + [""] * (per_row - len(chunk)))

    t = Table(rows, colWidths=[56 * mm] * per_row)
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), SIM_TINT if sim else TINT),
        ("TEXTCOLOR", (0, 0), (-1, -1), SIM_INK if sim else INK),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 3, colors.white),
    ]
    for r in range(0, len(rows), 2):
        style.append(("FONT", (0, r), (-1, r), "Helvetica-Bold", 15))
        style.append(("TOPPADDING", (0, r), (-1, r), 9))
        if r + 1 < len(rows):
            style.append(("FONT", (0, r + 1), (-1, r + 1), "Helvetica", 8))
            style.append(("TEXTCOLOR", (0, r + 1), (-1, r + 1), MUTED))
            style.append(("BOTTOMPADDING", (0, r + 1), (-1, r + 1), 9))
    t.setStyle(TableStyle(style))
    return t


def _table(spec: dict, styles: dict) -> Table:
    header = [Paragraph(f"<b>{h}</b>", styles["small"]) for h in spec["headers"]]
    body = [[Paragraph(str(c), styles["small"]) for c in row]
            for row in spec["rows"]]

    t = Table([header] + body, repeatRows=1, hAlign="LEFT")
    style = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]
    # Tint any row containing a projected figure, so the distinction is
    # visible while skimming rather than only in the legend.
    for i, row in enumerate(spec["rows"], start=1):
        if any(RC.MARK["projected"] in str(c) for c in row):
            style.append(("BACKGROUND", (0, i), (-1, i), TINT))
    t.setStyle(TableStyle(style))
    return t


def _banner(text: str, styles: dict) -> Table:
    t = Table([[Paragraph(f"<b>{text}</b>", styles["banner"])]],
              colWidths=[170 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SIM_TINT),
        ("LINEBEFORE", (0, 0), (0, -1), 3, AMBER),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _explanation(text: str, styles: dict, valid: bool = True) -> Table:
    """AI-written caption beneath a chart, visually distinct from the data.

    Marked so a reader always knows which prose was generated. An
    unvalidated explanation is flagged in red rather than silently shown -
    it may contain a figure that was not checked against the chart.
    """
    label = "AI explanation" if valid else "AI explanation (UNVALIDATED)"
    body = Paragraph(f"<b>{label}.</b> {text}", styles["small"])
    t = Table([[body]], colWidths=[165 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TINT),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, BLUE if valid else RED),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _chart(name: str) -> Image | None:
    try:
        import charts
        png = charts.render_matplotlib(name, stamp=False, dpi=130)
    except Exception:
        return None
    img = Image(io.BytesIO(png))
    img.drawWidth = 165 * mm
    img.drawHeight = img.drawWidth * (5.2 / 9.0)
    img.hAlign = "LEFT"
    return img


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 12 * mm,
                      "Single snapshot - no time dimension. "
                      "† projected from assumptions  ‡ simulated history")
    canvas.drawRightString(190 * mm, 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 16 * mm, 190 * mm, 16 * mm)
    canvas.restoreState()


# ==========================================================================
def render(content: dict, path: str) -> str:
    st = _styles()
    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=22 * mm,
        title=content["title"], author="Telecom Decision Intelligence Platform")

    flow = []
    flow.append(Paragraph(content["title"], st["title"]))
    flow.append(Paragraph(
        f"Generated {datetime.now():%d %B %Y}  ·  "
        f"portfolio state, not a time series", st["subtitle"]))

    # --- legend, up front rather than buried at the end -------------------
    legend_rows = [[Paragraph(f"<b>{m}</b>", st["small"]),
                    Paragraph(d, st["small"])] for m, d in content["legend"]]
    lt = Table(legend_rows, colWidths=[22 * mm, 148 * mm], hAlign="LEFT")
    lt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TINT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    flow.append(lt)
    flow.append(Spacer(1, 4 * mm))

    # --- sections ---------------------------------------------------------
    for sec in content["sections"]:
        sim = sec["kind"] == "simulated"
        flow.append(Paragraph(sec["title"], st["h2sim" if sim else "h2"]))

        if sec.get("banner"):
            flow.append(_banner(sec["banner"], st))
            flow.append(Spacer(1, 4 * mm))

        if sec.get("tiles"):
            flow.append(_tiles(sec["tiles"], sim=sim))
            flow.append(Spacer(1, 4 * mm))

        narration = sec.get("narration")
        if narration and narration.get("text"):
            flow.append(Paragraph(narration["text"], st["body"]))
        elif narration and narration.get("error"):
            flow.append(Paragraph(
                f"<i>Narration unavailable: {narration['error']}</i>",
                st["small"]))

        if sec.get("table"):
            flow.append(Spacer(1, 2 * mm))
            flow.append(_table(sec["table"], st))
            flow.append(Spacer(1, 3 * mm))

        if sec.get("actions"):
            flow.append(Spacer(1, 2 * mm))
            flow.append(Paragraph("<b>Recommended actions</b>", st["body"]))
            action_rows = [[a["segment"], a["action"], a["offer"],
                            f"{a['customers']:,}"] for a in sec["actions"]]
            flow.append(_table({"headers": ["Segment", "Action", "Offer",
                                            "Customers"],
                                "rows": action_rows}, st))
            flow.append(Spacer(1, 3 * mm))

        if sec.get("assumptions"):
            a = sec["assumptions"]
            flow.append(Paragraph(
                f"<b>{RC.MARK['projected']} Assumptions behind projected "
                f"values:</b> save rate {a['assumed_save_rate']:.0%} "
                f"(band {a['save_rate_low']:.0%}-{a['save_rate_high']:.0%}), "
                f"contact cost ${a['retention_contact_cost']:.2f}, "
                f"acquisition cost ${a['acquisition_cost']:.2f}, "
                f"horizon {a['value_horizon_periods']} periods. "
                f"These are industry-typical placeholders, not measurements: "
                f"the dataset contains no cost data.", st["small"]))
            flow.append(Spacer(1, 3 * mm))

        if sec.get("bullets"):
            for b in sec["bullets"]:
                flow.append(Paragraph(f"•  {b}", st["bullet"]))

        if content["include_charts"] and sec.get("charts"):
            # AI explanation sits directly beneath its own chart. Keyed by
            # chart name rather than position, so reordering charts cannot
            # silently attach the wrong caption to the wrong figure.
            explanations = {e["chart"]: e
                            for e in sec.get("chart_explanations", [])}
            for name in sec["charts"]:
                img = _chart(name)
                if not img:
                    continue
                flow.append(Spacer(1, 4 * mm))
                flow.append(img)
                exp = explanations.get(name)
                if exp and exp.get("text"):
                    flow.append(Spacer(1, 1 * mm))
                    flow.append(_explanation(exp["text"], st,
                                             valid=exp.get("valid", False)))
                flow.append(Spacer(1, 3 * mm))

    # --- provenance -------------------------------------------------------
    flow.append(PageBreak())
    flow.append(Paragraph("Provenance", st["h2"]))
    rec = content["reconciliation"]
    flow.append(Paragraph(
        f"Every observed figure traces to the source data. Segment totals "
        f"reconcile to the portfolio: customers "
        f"{'match' if rec['customers']['match'] else 'DO NOT MATCH'} "
        f"({rec['customers']['portfolio']:,}), revenue "
        f"{'matches' if rec['revenue']['match'] else 'DOES NOT MATCH'} "
        f"(${rec['revenue']['portfolio']:,.2f}).", st["body"]))

    if not content["narration_valid"]:
        flow.append(Paragraph(
            f"<b>Warning:</b> narration in these sections failed "
            f"validation and may contain unverified figures: "
            f"{', '.join(content['invalid_sections'])}.", st["body"]))
    else:
        flow.append(Paragraph(
            "All narrative text passed automated validation: every figure "
            "quoted was checked against the data that produced it, and no "
            "claim about change over time was permitted.", st["body"]))

    bad_captions = content.get("invalid_captions") or []
    if bad_captions:
        flow.append(Paragraph(
            f"<b>Warning:</b> the AI explanation beneath these charts "
            f"failed validation and is marked in red: "
            f"{', '.join(bad_captions)}.", st["body"]))

    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return path


# ==========================================================================
def generate(path: str | None = None, include_charts: bool = True) -> str:
    import config as C
    if path is None:
        out = C.PROJECT_ROOT / "exports"
        out.mkdir(parents=True, exist_ok=True)
        path = str(out / f"portfolio_risk_report_"
                         f"{datetime.now():%Y%m%d_%H%M}.pdf")
    content = RC.build(include_charts=include_charts)
    return render(content, path)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    no_charts = "--no-charts" in sys.argv
    print("wrote", generate(target, include_charts=not no_charts))