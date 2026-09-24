"""Turn the AI-written compliance report into a PDF.

The PDF keeps facts and AI analysis apart, like the chat does:
  cover       tenant and scan details, and the verified pass/fail counts (from findings.json)
  body        the AI-written report (Markdown from Assistant.compliance_report), labelled as AI output
  appendix    the verified status of every control, straight from findings.json, no AI content

Uses fpdf2's built-in Helvetica, which only covers Latin-1, so the typographic characters models
like to use (curly quotes, dashes, bullets) are swapped for plain ones first.
"""
from datetime import datetime, timezone

import markdown
from fpdf import FPDF
from fpdf.fonts import FontFace, TextStyle

NAVY = (14, 27, 44)
MUTED = (74, 90, 112)
ACCENT = (36, 70, 168)
RED = (179, 38, 30)
GREEN = (31, 122, 90)
AMBER = (154, 90, 0)
PANEL = (234, 238, 244)
WHITE = (255, 255, 255)

REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": " - ",
    "‐": "-", "‑": "-", "−": "-", "…": "...", "•": "-", " ": " ",
    "→": "->", "≤": "<=", "≥": ">=", "≈": "~",
}


def plain(text):
    """Text safe for the built-in PDF fonts."""
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def format_time(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%d %B %Y, %H:%M UTC")
    except (TypeError, ValueError):
        return iso or "Unknown"


def status_label(f):
    """(label, colour). Keeps ScubaGear's Warning visible even though the Finding status is FAIL."""
    if f.status == "PASS":
        return "Pass", GREEN
    if f.status == "FAIL":
        return ("Warning", AMBER) if f.scuba_result.lower() == "warning" else ("Fail", RED)
    return "Not assessed", MUTED


class ReportPDF(FPDF):
    def __init__(self, running_title):
        super().__init__(format="A4")
        self.running_title = running_title
        self.set_margins(20, 20, 20)
        self.set_auto_page_break(True, margin=20)
        self.set_title("SCuBA compliance report")
        self.set_creator("SCuBA posture assistant")

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, self.running_title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*PANEL)
        self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
        self.ln(6)

    def footer(self):
        self.set_y(-13)
        self.set_font("helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, "AI-assisted report. Statuses come from the OSCAL assessment results.")
        self.set_x(self.l_margin)
        self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="R")

    def heading(self, text, size=15):
        self.set_font("helvetica", "B", size)
        self.set_text_color(*NAVY)
        self.multi_cell(0, size * 0.5, plain(text), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def note(self, text, color=MUTED, fill=PANEL):
        self.set_font("helvetica", "", 9)
        self.set_text_color(*color)
        self.set_fill_color(*fill)
        self.multi_cell(0, 4.8, plain(text), fill=True, padding=3, new_x="LMARGIN", new_y="NEXT")
        self.ln(4)


def _cover(pdf, info, summary, generated_at):
    pdf.set_font("helvetica", "B", 24)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 12, "SCuBA compliance report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 12)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 7, "Microsoft Entra ID against the CISA SCuBA MS.AAD baseline", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    details = [
        ("Tenant", f"{info.get('tenant') or 'Unknown'} ({info.get('domain') or 'unknown domain'})"),
        ("Scan date", format_time(info.get("scan_time"))),
        ("Scanner", f"ScubaGear {info.get('tool_version') or 'unknown version'}"),
        ("Report generated", generated_at.strftime("%d %B %Y, %H:%M UTC")),
    ]
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(*NAVY)
    with pdf.table(col_widths=(40, 130), first_row_as_headings=False, borders_layout="NONE",
                   text_align="LEFT", line_height=6, align="LEFT") as table:
        for label, value in details:
            row = table.row()
            row.cell(label, style=FontFace(emphasis="B", color=MUTED))
            row.cell(plain(value))
    pdf.ln(6)

    tiles = [("Passing", summary["passed"], GREEN), ("Failing", summary["failed"], RED),
             ("Not assessed", summary["not_assessed"], MUTED), ("Total controls", summary["total"], NAVY)]
    pdf.set_draw_color(*PANEL)
    with pdf.table(text_align="CENTER", line_height=7, padding=2,
                   headings_style=FontFace(emphasis="B", size_pt=9, color=MUTED, fill_color=PANEL)) as table:
        table.row([label for label, _, _ in tiles])
        row = table.row()
        for _, value, color in tiles:
            row.cell(str(value), style=FontFace(emphasis="B", size_pt=18, color=color))
    pdf.ln(8)


def _body(pdf, reply):
    pdf.note("The report below was written by an AI model from the verified OSCAL results. Pass and fail "
             "statuses come from the ScubaGear scan; explanations, risk statements and recommendations are "
             "AI analysis. Check them against the appendix before acting on them.")
    if reply.get("unverified_references"):
        pdf.note("The AI mentioned controls that are not part of this assessment, so they are not verified: "
                 + ", ".join(reply["unverified_references"]) + ".", color=AMBER, fill=(253, 243, 224))

    html = markdown.markdown(plain(reply["text"]), extensions=["sane_lists"])
    pdf.set_font("helvetica", "", 10.5)  # list items take the current font size
    pdf.set_text_color(*NAVY)
    pdf.write_html(
        html,
        font_family="helvetica",
        li_prefix_color=ACCENT,
        ul_bullet_char="-",
        # Margins here are in fpdf2's HTML units (roughly lines), not millimetres.
        tag_styles={
            "h1": TextStyle(font_family="helvetica", font_style="B", font_size_pt=17, color=NAVY, t_margin=5, b_margin=0.4),
            "h2": TextStyle(font_family="helvetica", font_style="B", font_size_pt=15, color=NAVY, t_margin=5, b_margin=0.4),
            "h3": TextStyle(font_family="helvetica", font_style="B", font_size_pt=12, color=ACCENT, t_margin=4, b_margin=0.2),
            "h4": TextStyle(font_family="helvetica", font_style="B", font_size_pt=11, color=NAVY, t_margin=3, b_margin=0.2),
            "p": TextStyle(font_family="helvetica", font_size_pt=10.5, color=NAVY, t_margin=2),
            "ul": TextStyle(font_family="helvetica", font_size_pt=10.5, color=NAVY, t_margin=1.5),
            "ol": TextStyle(font_family="helvetica", font_size_pt=10.5, color=NAVY, t_margin=1.5),
            "li": TextStyle(font_family="helvetica", font_size_pt=10.5, color=NAVY, t_margin=1.5, l_margin=6),
            "code": FontFace(family="helvetica", color=ACCENT),  # control ids: text font, accent colour
        },
    )


def _appendix(pdf, findings):
    pdf.add_page()
    pdf.heading("Appendix: verified control results")
    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 4.8, "Taken directly from findings.json, the deterministic comparison of the SCuBA catalog "
                           "with the ScubaGear assessment results. This appendix contains no AI-generated content. "
                           "Warning is ScubaGear's result for a failed SHOULD requirement.",
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(*NAVY)
    pdf.set_draw_color(*PANEL)
    pdf.set_fill_color(*WHITE)  # tables reuse the current fill colour
    with pdf.table(col_widths=(27, 73, 24, 24, 22), text_align="LEFT", line_height=5, padding=1.5,
                   headings_style=FontFace(emphasis="B", color=WHITE, fill_color=NAVY)) as table:
        table.row(["Control", "Title", "Obligation", "Status", "Priority"])
        for f in findings:
            label, color = status_label(f)
            row = table.row()
            row.cell(f.control_id)
            row.cell(plain(f.title))
            row.cell(f.obligation or "-")
            row.cell(label, style=FontFace(emphasis="B", color=color))
            row.cell((f.priority or "-").capitalize())


def build_pdf(reply, findings, info, summary, generated_at=None):
    """reply: the dict from Assistant.compliance_report(). Returns the PDF as bytes."""
    generated_at = generated_at or datetime.now(timezone.utc)
    pdf = ReportPDF(plain(f"SCuBA compliance report  |  {info.get('tenant') or ''}  |  "
                          f"{generated_at.strftime('%d %B %Y')}"))
    pdf.add_page()
    _cover(pdf, info, summary, generated_at)
    _body(pdf, reply)
    _appendix(pdf, findings)
    return bytes(pdf.output())
