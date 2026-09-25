"""Build the compliance report PDF.

Facts and AI analysis are kept apart, as in the chat. The layout is fixed here; the AI only fills
in prose (Assistant.compliance_report returns it as structured data):

  cover                   tenant and scan details, verified counts             facts
  executive summary       overall posture and top risks                        AI analysis
  recommended next steps  numbered actions                                     AI analysis
  scope and method        what was assessed and how                            fixed text
  findings                one card per failed control, high priority first:
                            control, title, priority / result / obligation tags,
                            requirement and scan result                         facts
                            why it matters, how to fix                          AI analysis
  controls that passed    table                                                facts
  not assessed            table                                                facts
  appendix                every control's status                               facts

If the AI's reply could not be read (reply["report"] is None) the report still has every fact
section, and each finding shows the SCuBA remediation guidance instead of the AI's notes.

Uses fpdf2's built-in Helvetica, which only covers Latin-1, so the typographic characters models
like to use (curly quotes, dashes, bullets) are swapped for plain ones first.
"""
import re
from datetime import datetime, timezone

from fpdf import FPDF
from fpdf.fonts import FontFace

# Same palette as the app (.streamlit/config.toml): ink and paper, colour only for results.
NAVY = (28, 39, 51)        # ink #1C2733
TEXT = (43, 55, 68)
MUTED = (95, 107, 122)     # #5F6B7A
ACCENT = (61, 90, 115)     # slate #3D5A73: AI labels and list numbers, never a result colour
RED = (180, 35, 24)        # #B42318 failed SHALL
RED_BG = (248, 228, 225)
AMBER = (178, 94, 9)       # #B25E09 ScubaGear warning
AMBER_BG = (249, 235, 219)
GREEN = (47, 122, 88)      # #2F7A58 passed
GREEN_BG = (227, 239, 232)
GRAY_BG = (236, 238, 234)
PANEL = (238, 241, 243)    # AI note background, as in the app
RULE = (212, 217, 210)     # #D4D9D2
ZEBRA = (246, 247, 245)
WHITE = (255, 255, 255)

BODY_PT, BODY_H = 10.5, 5.4           # body text size (pt) and line height (mm), about 1.45 spacing
REMEDIATION_LIMIT = 700                # characters of SCuBA guidance shown when the AI's notes are missing

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


def rich(text):
    """AI prose for fpdf's markdown mode: `ids` become bold, stray markers can't switch styles."""
    text = plain(text).replace("--", "-").replace("__", "_")
    return re.sub(r"`([^`]+)`", r"**\1**", text)


def format_time(iso, fmt="%d %B %Y, %H:%M UTC"):
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime(fmt)
    except (TypeError, ValueError):
        return iso or "Unknown"


def result_tag(f):
    """(label, text colour, background) for a control's result; keeps ScubaGear's Warning visible."""
    if f.status == "PASS":
        return "Pass", GREEN, GREEN_BG
    if f.status == "FAIL":
        return ("Warning", AMBER, AMBER_BG) if f.scuba_result.lower() == "warning" else ("Fail", RED, RED_BG)
    return "Not assessed", MUTED, GRAY_BG


def priority_tag(priority):
    return {"high": ("High priority", RED, RED_BG), "moderate": ("Moderate priority", AMBER, AMBER_BG)}[priority]


class ReportPDF(FPDF):
    def __init__(self, running_title):
        super().__init__(format="A4")
        self.running_title = running_title
        self.set_margins(20, 18, 20)
        self.set_auto_page_break(True, margin=20)
        self.set_title("SCuBA compliance report")
        self.set_creator("SCuBA posture assistant")

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, self.running_title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*RULE)
        self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
        self.ln(7)

    def footer(self):
        self.set_y(-13)
        self.set_font("helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, "Statuses come from the ScubaGear scan. Sections marked AI analysis are AI-written.")
        self.set_x(self.l_margin)
        self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="R")

    # --- building blocks ---------------------------------------------------------------------
    def room_for(self, height):
        """Start a new page unless `height` mm still fits, so headings never sit alone at the bottom."""
        if self.get_y() + height > self.page_break_trigger:
            self.add_page()

    def tag(self, text, fg, bg, size=7.5):
        """A small rounded label at the current position; moves right past it."""
        self.set_font("helvetica", "B", size)
        w, h = self.get_string_width(text) + 4.5, 5
        x, y = self.get_x(), self.get_y()
        self.set_fill_color(*bg)
        self.rect(x, y, w, h, style="F", round_corners=True, corner_radius=1.3)
        self.set_text_color(*fg)
        self.cell(w, h, text, align="C")
        self.set_x(x + w + 2)

    def section(self, title, source=None, keep=28):
        """A section heading, optionally with a tag saying where its content comes from.
        `keep` is how much room (mm) must follow on the same page, so a heading never ends a page."""
        self.room_for(keep)
        self.ln(3)
        y = self.get_y()
        self.set_font("helvetica", "B", 15)
        self.set_text_color(*NAVY)
        self.cell(self.get_string_width(title) + 3, 8, title)
        if source:
            self.set_xy(self.get_x(), y + 1.6)   # centre the tag on the heading line
            if source == "ai":
                self.tag("AI analysis", ACCENT, PANEL)
            else:
                self.tag("Verified results", GREEN, GREEN_BG)
        self.set_xy(self.l_margin, y + 10)
        self.set_draw_color(*RULE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3.5)

    def subsection(self, title, count, color):
        self.room_for(40)
        self.ln(2)
        self.set_fill_color(*color)
        self.rect(self.l_margin, self.get_y() + 1.2, 2.5, 4.6, style="F")
        self.set_x(self.l_margin + 5)
        self.set_font("helvetica", "B", 12)
        self.set_text_color(*NAVY)
        self.cell(0, 7, f"{title} ({count})", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def para(self, text, size=BODY_PT, h=BODY_H, color=TEXT, x=None, w=0, gap=2.5):
        self.set_font("helvetica", "", size)
        self.set_text_color(*color)
        if x is not None:
            self.set_x(x)
        self.multi_cell(w, h, rich(text), markdown=True, align="L", new_x="LMARGIN", new_y="NEXT")
        self.ln(gap)

    def numbered(self, items, x=None, w=None, size=BODY_PT, h=BODY_H):
        x = self.l_margin if x is None else x
        w = self.epw - (x - self.l_margin) if w is None else w
        for i, item in enumerate(items, 1):
            self.set_x(x)
            self.set_font("helvetica", "B", size)
            self.set_text_color(*ACCENT)
            self.cell(7, h, f"{i}.")
            self.set_font("helvetica", "", size)
            self.set_text_color(*TEXT)
            self.multi_cell(w - 7, h, rich(item), markdown=True, align="L", new_x="LMARGIN", new_y="NEXT")
            self.ln(1.5)

    def note(self, text, color=MUTED, fill=PANEL):
        self.set_font("helvetica", "", 9)
        self.set_text_color(*color)
        self.set_fill_color(*fill)
        self.multi_cell(0, 4.8, plain(text), fill=True, padding=3.5, align="L", new_x="LMARGIN", new_y="NEXT")
        self.ln(4)

    def control_table(self, rows, headings, col_widths, style_status=False):
        """rows: lists of cell text; with style_status, the 4th cell is a Finding for a coloured status."""
        self.set_font("helvetica", "", 9)
        self.set_text_color(*TEXT)
        self.set_draw_color(*RULE)
        self.set_fill_color(*WHITE)   # rows between the zebra stripes use the current fill colour
        with self.table(col_widths=col_widths, text_align="LEFT", line_height=5, padding=(1.8, 2),
                        cell_fill_color=ZEBRA, cell_fill_mode="ROWS", borders_layout="HORIZONTAL_LINES",
                        headings_style=FontFace(emphasis="B", color=WHITE, fill_color=NAVY)) as table:
            table.row(headings)
            for cells in rows:
                row = table.row()
                for i, value in enumerate(cells):
                    if style_status and i == 3:
                        label, fg, _ = result_tag(value)
                        row.cell(label, style=FontFace(emphasis="B", color=fg))
                    else:
                        row.cell(plain(str(value)))
        self.ln(4)


# --- sections ------------------------------------------------------------------------------------
def _cover(pdf, info, summary, failures, generated_at):
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

    tiles = [("Passing", f"{summary['passed']} of {summary['assessed']}", GREEN),
             ("Failing", summary["failed"], RED),
             ("Not assessed", summary["not_assessed"], MUTED),
             ("Total controls", summary["total"], NAVY)]
    pdf.set_draw_color(*RULE)
    with pdf.table(text_align="CENTER", line_height=7, padding=2,
                   headings_style=FontFace(emphasis="B", size_pt=9, color=MUTED, fill_color=PANEL)) as table:
        table.row([label for label, _, _ in tiles])
        row = table.row()
        for _, value, color in tiles:
            row.cell(str(value), style=FontFace(emphasis="B", size_pt=18, color=color))
    pdf.ln(3)

    high = sum(f.priority == "high" for f in failures)
    moderate = len(failures) - high
    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(pdf.get_string_width("Failing controls by priority:") + 3, 5, "Failing controls by priority:")
    pdf.tag(f"{high} high", RED, RED_BG)
    pdf.tag(f"{moderate} moderate", AMBER, AMBER_BG)
    pdf.ln(10)

    pdf.note("Sections marked AI analysis were written by an AI model from the verified results and are "
             "analysis, not findings. Every status, requirement and scan result comes directly from the "
             "ScubaGear scan; check the AI's notes against them before acting.")


def _scope(pdf, info, summary):
    pdf.section("Scope and method")
    pdf.para(f"This report assesses the {info.get('tenant') or 'unknown'} tenant "
             f"({info.get('domain') or 'unknown domain'}) against the CISA SCuBA baseline for Microsoft Entra ID "
             f"(MS.AAD), using a ScubaGear {info.get('tool_version') or ''} scan run on "
             f"{format_time(info.get('scan_time'), '%d %B %Y')}. The scan results were converted to OSCAL and "
             f"compared, control by control, with the {summary['total']}-control SCuBA catalog.")
    pdf.para("Every status, requirement and scan result in this report comes from that comparison; the AI does "
             "not decide whether a control passed. Priority follows each requirement's wording: a failed SHALL "
             "or SHALL NOT requirement is high priority, and a failed SHOULD requirement (reported by ScubaGear "
             "as a Warning) is moderate. Controls ScubaGear did not evaluate are listed as not assessed.")


def _card(pdf, f, analysis):
    """One failed control. Facts first, then the AI's notes (or the SCuBA guidance if there are none)."""
    x0, y0, page0 = pdf.l_margin, pdf.get_y(), pdf.page
    inner_x, inner_w = x0 + 6, pdf.epw - 10
    label_w = 27
    pdf.set_y(y0 + 3.5)

    pdf.set_x(inner_x)
    pdf.set_font("helvetica", "B", 11.5)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(inner_w, 6, plain(f"{f.control_id}   {f.title}"), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_x(inner_x)
    pdf.tag(*priority_tag(f.priority))
    label, fg, bg = result_tag(f)
    pdf.tag(f"ScubaGear: {label}", fg, bg)
    pdf.tag(f.obligation, MUTED, GRAY_BG)
    pdf.ln(8)

    def field(name, text, color=TEXT, serif=False):
        pdf.set_x(inner_x)
        pdf.set_font("helvetica", "B", 8.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(label_w, BODY_H - 0.4, name)
        pdf.set_font("times" if serif else "helvetica", "", 10.5 if serif else 9.5)   # the baseline's own words in serif
        pdf.set_text_color(*color)
        pdf.multi_cell(inner_w - label_w, BODY_H - 0.4, rich(text), markdown=True, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)

    field("Requirement", f.requirement, serif=True)
    field("Scan result", f.finding or "No details recorded.")

    if analysis and (analysis.get("why_it_matters") or analysis.get("how_to_fix")):
        pdf.ln(1)
        pdf.set_x(inner_x)
        pdf.tag("AI analysis", ACCENT, PANEL, size=7)
        pdf.ln(7)
        if analysis.get("why_it_matters"):
            field("Why it matters", analysis["why_it_matters"])
        if analysis.get("how_to_fix"):
            pdf.set_x(inner_x)
            pdf.set_font("helvetica", "B", 8.5)
            pdf.set_text_color(*MUTED)
            pdf.cell(label_w, BODY_H - 0.4, "How to fix")   # the steps start on the same line
            pdf.numbered(analysis["how_to_fix"], x=inner_x + label_w, w=inner_w - label_w, size=9.5, h=BODY_H - 0.4)
    else:
        guidance = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", f.remediation)   # [text](url) -> text
        guidance = " ".join(re.sub(r"(?m)^\s*>\s?", "", guidance).split())   # drop "> " quote markers
        if len(guidance) > REMEDIATION_LIMIT:
            guidance = guidance[:REMEDIATION_LIMIT].rsplit(" ", 1)[0] + " ... (see the SCuBA baseline for the full steps)"
        field("SCuBA guidance", guidance or "None recorded.")

    y1 = pdf.get_y() + 2
    if pdf.page == page0:   # frame the card; skipped only if a card is longer than a whole page
        pdf.set_draw_color(*RULE)
        pdf.rect(x0, y0, pdf.epw, y1 - y0, style="D", round_corners=True, corner_radius=2)
        pdf.set_fill_color(*(RED if f.priority == "high" else AMBER))
        pdf.rect(x0, y0, 1.8, y1 - y0, style="F")
    pdf.set_y(y1 + 4)


def _findings(pdf, failures, analyses):
    pdf.section("Findings", keep=70)
    if not failures:
        pdf.para("No assessed control failed.")
        return
    intro = ("One card per failed control, highest priority first. The requirement and scan result come from "
             "the scan; ")
    intro += ("the notes under AI analysis are the AI's explanation and suggested fix." if analyses else
              "the guidance is the SCuBA baseline's remediation text.")
    pdf.para(intro, size=9.5, color=MUTED, gap=3)
    for level, title, color in (("high", "High priority", RED), ("moderate", "Moderate priority", AMBER)):
        group = [f for f in failures if f.priority == level]
        if not group:
            continue
        pdf.subsection(title, len(group), color)
        for f in group:
            with pdf.offset_rendering() as dummy:   # keep each card on one page
                _card(dummy, f, analyses.get(f.control_id))
            if dummy.page_break_triggered:
                pdf.add_page()
            _card(pdf, f, analyses.get(f.control_id))


def _lists(pdf, findings):
    passing = [f for f in findings if f.status == "PASS"]
    unassessed = [f for f in findings if f.status == "NOT ASSESSED"]
    if passing:
        pdf.section("Controls that passed", "facts", keep=55)
        pdf.control_table([[f.control_id, f.title, f.obligation] for f in passing],
                          ["Control", "Title", "Obligation"], (30, 116, 24))
    if unassessed:
        pdf.section("Not assessed", "facts", keep=70)
        pdf.para("ScubaGear returned no result for these controls, either because it cannot check them yet or "
                 "because they are newer than the ScubaGear version that ran. Their status is unknown; check "
                 "them manually.", size=9.5, color=MUTED, gap=3)
        pdf.control_table([[f.control_id, f.title, f.obligation] for f in unassessed],
                          ["Control", "Title", "Obligation"], (30, 116, 24))


def _appendix(pdf, findings):
    pdf.add_page()
    pdf.section("Appendix: all controls", "facts")
    pdf.para("Every control in the catalog with its result, straight from the scan. This appendix contains no "
             "AI-generated content.", size=9.5, color=MUTED, gap=2)
    pdf.set_font("helvetica", "", 8.5)
    pdf.set_text_color(*MUTED)
    for text, fg, bg in (("Pass", GREEN, GREEN_BG), ("Fail", RED, RED_BG), ("Warning", AMBER, AMBER_BG),
                         ("Not assessed", MUTED, GRAY_BG)):
        pdf.tag(text, fg, bg)
    pdf.ln(6)
    pdf.set_font("helvetica", "", 8.5)
    pdf.multi_cell(0, 4.5, "Fail: a SHALL or SHALL NOT requirement is not met.   Warning: a SHOULD requirement is "
                           "not met.   Not assessed: ScubaGear returned no result.", align="L",
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.control_table([[f.control_id, f.title, f.obligation, f, (f.priority or "-").capitalize()] for f in findings],
                      ["Control", "Title", "Obligation", "Status", "Priority"], (26, 71, 24, 28, 21), style_status=True)


def build_pdf(reply, findings, info, summary, generated_at=None):
    """reply: the dict from Assistant.compliance_report(). Returns the PDF as bytes."""
    generated_at = generated_at or datetime.now(timezone.utc)
    report = reply.get("report")
    failures = sorted((f for f in findings if f.status == "FAIL"), key=lambda f: f.priority != "high")
    pdf = ReportPDF(plain(f"SCuBA compliance report  |  {info.get('tenant') or ''}  |  "
                          f"{generated_at.strftime('%d %B %Y')}"))
    pdf.add_page()
    _cover(pdf, info, summary, failures, generated_at)

    if report is None:
        pdf.note("The AI's analysis could not be read, so this report shows the verified results only. Try "
                 "generating it again.", color=AMBER, fill=AMBER_BG)
    if reply.get("unverified_references"):
        pdf.note("The AI mentioned controls that are not part of this assessment, so they are not verified: "
                 + ", ".join(reply["unverified_references"]) + ".", color=AMBER, fill=AMBER_BG)
    if report and report["executive_summary"]:
        pdf.section("Executive summary", "ai")
        for paragraph in report["executive_summary"]:
            pdf.para(paragraph)
    if report and report["next_steps"]:
        pdf.section("Recommended next steps", "ai")
        pdf.numbered(report["next_steps"])
    _scope(pdf, info, summary)
    _findings(pdf, failures, (report or {}).get("findings", {}))
    _lists(pdf, findings)
    _appendix(pdf, findings)
    return bytes(pdf.output())
