"""SCuBA posture assistant: the chat frontend for the AI layer in ai/.

Run from the repo root:  streamlit run app/app.py

Everything typed in the chat goes to ai.answer(question, history). Each reply shows the AI's
analysis, labelled as AI output, next to the verified OSCAL facts for every control it cites.
The scan summary comes from the same verified records the AI reads (ai/findings.py), so the page
and the AI always agree. The compliance report is written by the AI (Assistant.compliance_report)
and turned into a PDF by report_pdf.py. Azure OpenAI settings come from .env (see .env.example).

There is no default scan: the page opens on an upload panel, and the user uploads their ScubaGear
results (.json) or tries the sample in data/sample/. The file is checked against the SCuBA catalogs
in oscal/Controls by the pipeline (pipeline/make_assessment_results.py, then
comparison/compare_oscal.py) in a temporary folder that is deleted afterwards. The result lives only
in that browser session; a refresh starts over. Controls the scan has no record for are NOT_ASSESSED.
"""
import html
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT), str(HERE)]

from ai.assistant import ENV_VARS, Assistant  # noqa: E402
from ai.findings import prioritized_failures  # noqa: E402
from report_pdf import build_pdf  # noqa: E402

log = logging.getLogger(__name__)

PAGE_ICON = ":material/shield_lock:"

STATUS_BADGE = {  # status -> (label, color, icon)
    "PASS": ("Pass", "green", ":material/check_circle:"),
    "FAIL": ("Fail", "red", ":material/cancel:"),
    "NOT ASSESSED": ("Not assessed", "gray", ":material/help:"),
}
WARNING_BADGE = ("Warning", "orange", ":material/warning:")   # ScubaGear's word for a failed SHOULD
PRIORITY_BADGE = {"high": ("High priority", "red"), "moderate": ("Moderate priority", "orange")}

TAB_OVERVIEW = "Overview"
TAB_FINDINGS = "Findings"
TAB_CHAT = "Questions"
TAB_REPORT = "Report"
FIX_FIRST = 3   # high-priority failures shown on the overview
POLICY_ID = re.compile(r"^MS\.[A-Z]+\.(\d+)\.(\d+)v\d+$")   # MS.AAD.<section>.<policy>v<version>
MAP_CLASS = {"Fail": "fail", "Warning": "warn", "Pass": "pass", "Not assessed": "na"}

# Colours and type beyond what .streamlit/config.toml can express: the baseline map cells (styled by
# their widget key, st-key-map_<result>_...), the legend swatches, and the baseline's requirement
# text quoted in Merriweather so the standard's own words read differently from the interface.
STYLE = """<style>
@import url('https://fonts.googleapis.com/css2?family=Merriweather:wght@400;700&display=swap');
.scb-req { font-family: Merriweather, Georgia, serif; font-size: 0.98rem; line-height: 1.65; color: #1C2733;
           margin: 0.15rem 0 0.5rem; padding-left: 0.85rem; border-left: 3px solid #D4D9D2; max-width: 75ch; }
.scb-req-sm { font-size: 0.88rem; line-height: 1.6; }
.scb-verdict { font-size: 1.5rem; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em; color: #1C2733;
               margin: 0.4rem 0 0.35rem; max-width: 62ch; }
.scb-dialog-title { font-size: 1.3rem; font-weight: 600; line-height: 1.35; color: #1C2733; margin: 0 0 0.2rem; }
.scb-legend { display: flex; flex-wrap: wrap; gap: 0.4rem 1.3rem; font-size: 0.85rem; color: #5F6B7A; margin: 0 0 0.6rem; }
.scb-legend span { display: inline-flex; align-items: center; gap: 0.45rem; }
.scb-legend i { width: 0.95rem; height: 0.95rem; border-radius: 2px; display: inline-block; }
.scb-sw-fail { background: #B42318; }
.scb-sw-warn { background: #B25E09; }
.scb-sw-pass { background: #E3EFE8; box-shadow: inset 0 0 0 1px #BFD8C8; }
.scb-sw-na { background: repeating-linear-gradient(135deg, #ECEEEA 0 3px, #C9CEC6 3px 5px); box-shadow: inset 0 0 0 1px #C9CEC6; }
[class*="st-key-map_"] button { width: 3rem; min-height: 2.3rem; padding: 0; border-radius: 3px; }
[class*="st-key-map_"] button p { font-size: 0.8rem; font-weight: 700; font-variant-numeric: tabular-nums; }
[class*="st-key-map_fail_"] button, [class*="st-key-map_fail_"] button:hover {
    background: #B42318; border-color: #B42318; color: #FFFFFF; }
[class*="st-key-map_warn_"] button, [class*="st-key-map_warn_"] button:hover {
    background: #B25E09; border-color: #B25E09; color: #FFFFFF; }
[class*="st-key-map_pass_"] button, [class*="st-key-map_pass_"] button:hover {
    background: #E3EFE8; border-color: #BFD8C8; color: #2F7A58; }
[class*="st-key-map_na_"] button, [class*="st-key-map_na_"] button:hover {
    background: repeating-linear-gradient(135deg, #ECEEEA 0 4px, #D9DDD5 4px 6px); border-color: #C9CEC6; color: #5F6B7A; }
[class*="st-key-map_"] button:hover { filter: brightness(0.92); }
[class*="st-key-map_"] button:focus-visible { outline: 2px solid #1C2733; outline-offset: 2px; }
[class*="st-key-row_"] details { border: none; border-bottom: 1px solid #D4D9D2; border-radius: 0; background: transparent; }
[class*="st-key-row_"] summary { padding-top: 0.6rem; padding-bottom: 0.6rem; }
[class*="st-key-row_"] { border-left: 4px solid transparent; }
[class*="st-key-row_fail_"] { border-left-color: #B42318; }
[class*="st-key-row_warn_"] { border-left-color: #B25E09; }
[class*="st-key-note_"] { background: #EEF1F3; border-radius: 4px; padding: 0.75rem 1rem; }
.scb-note-label { font-size: 0.85rem; font-weight: 700; color: #3D5A73; margin: 0 0 0.25rem; }
.scb-q { font-size: 1.12rem; font-weight: 700; line-height: 1.4; color: #1C2733; margin: 0.4rem 0 0.35rem; max-width: 70ch; }
.scb-rule { border: none; border-top: 1px solid #D4D9D2; margin: 1.1rem 0 0.6rem; }
.scb-dl, .scb-dl dt, .scb-dl dd { margin-left: 0; padding-left: 0; }
.scb-dl { margin: 0; }
.scb-dl dt { font-size: 0.75rem; color: #5F6B7A; margin-top: 0.55rem; }
.scb-dl dd { margin: 0; font-size: 0.92rem; color: #1C2733; overflow-wrap: anywhere; }
.scb-dl dd span { color: #5F6B7A; }
</style>"""

EXAMPLE_QUESTIONS = [
    "What should we fix first, and why?",
    "What is our biggest identity risk right now?",
    "How are we doing on privileged access?",
]
NOTE_REQUEST = (
    "Write a short analyst note on the failed control `{control_id}` for this tenant, in at most 120 words: "
    "why this failure matters (as analysis, not fact) and how to approach the fix, in priority order. Do not "
    "restate the requirement or the scan result; they are shown next to your note.")

GENERIC_ERROR = ("The AI service didn't return an answer. Check the Azure OpenAI settings in `.env` "
                 "and your network connection, then try again.")

CONTROLS_DIR = ROOT / "oscal" / "Controls"   # the SCuBA catalogs an uploaded scan is checked against
SAMPLE_SCAN = ROOT / "data" / "sample" / "scuba_results_sample.json"
MAX_UPLOAD_MB = 50
STALE_SCAN_DAYS = 30   # older scans get a "re-run ScubaGear" note
NOT_ASSESSED_REASONS = {   # ScubaGear's Result -> why the control has no pass/fail
    "N/A": "ScubaGear can't check this policy automatically.",
    "Error": "ScubaGear hit an error checking this policy; re-run the scan.",
    "Omitted": "Left out of the scan by the ScubaGear configuration.",
}
PIPELINE_TIMEOUT = 180  # seconds per step


# --- AI -----------------------------------------------------------------------
def ai_problem():
    """Why the AI can't be reached, or None. Checks setup only; never reads credential values out."""
    if importlib.util.find_spec("openai") is None:
        return "The `openai` package is not installed in this environment. Run `pip install openai`."
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)  # pick up .env edits without restarting the server
    missing = [v for v in ENV_VARS if not os.environ.get(v)]
    if missing:
        return f"Azure OpenAI is not configured. Add {', '.join(missing)} to `.env` (see `.env.example`)."
    return None


def friendly_error(exc):
    if isinstance(exc, RuntimeError) and "not configured" in str(exc):
        return str(exc)  # names the missing settings only, never their values
    if isinstance(exc, ImportError):
        return "The `openai` package is not installed in this environment. Run `pip install openai`."
    return GENERIC_ERROR


def chat_history():
    """Earlier turns sent with a question so follow-ups work. Failed turns are left out."""
    return [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages
            if not m.get("error") and not m.get("failed")]


def ask(assistant, request, history):
    if request["kind"] == "explain":
        return assistant.explain(request["control_id"])
    if request["kind"] == "summary":
        return assistant.executive_summary()
    return assistant.answer(request["prompt"], history)


# --- Uploaded ScubaGear results -------------------------------------------------
def check_scan(name, raw):
    """The parsed file if it is a ScubaGear results JSON file; raises ValueError otherwise."""
    if not name.lower().endswith(".json"):
        raise ValueError(f"{name} is not a .json file. Upload the ScubaResults JSON file that ScubaGear produced.")
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"{name} is not valid JSON. It may be damaged or not a ScubaGear file.") from None
    if not isinstance(doc, dict) or not {"MetaData", "Results"} <= doc.keys():
        raise ValueError(f"{name} is JSON, but not a ScubaGear results report (it has no MetaData "
                         "and Results sections). Upload the ScubaResults JSON file from your ScubaGear output.")
    return doc


def scan_results(doc):
    """ScubaGear's own Result for every policy in the scan, e.g. {"MS.AAD.2.2v1": "N/A"}. Used to say
    why a control is not assessed; the pipeline itself only keeps the controls it could judge."""
    out = {}
    for groups in (doc.get("Results") or {}).values():
        for group in groups if isinstance(groups, list) else []:
            for c in group.get("Controls", []) if isinstance(group, dict) else []:
                if isinstance(c, dict) and c.get("Control ID"):
                    out[c["Control ID"]] = c.get("Result") or ""
    return out


def not_assessed_reason(control_id, results):
    result = results.get(control_id)
    if result is None:
        return "Not in the scan: newer than this ScubaGear version."
    return NOT_ASSESSED_REASONS.get(result, f"ScubaGear returned {result!r}, which is not a pass or fail.")


def combine_catalogs(folder, dest):
    """Write the SCuBA catalogs in `folder` (oscal/Controls) as one catalog file, which is what the
    assessment results builder takes. With a single catalog this is a plain copy."""
    files = sorted(Path(folder).glob("*.json"))
    if not files:
        raise ValueError(f"No SCuBA catalogs found in {folder}.")
    if len(files) == 1:
        shutil.copy(files[0], dest)
        return
    combined = json.loads(files[0].read_text(encoding="utf-8-sig"))
    for f in files[1:]:
        combined["catalog"]["groups"] += json.loads(f.read_text(encoding="utf-8-sig"))["catalog"].get("groups", [])
    Path(dest).write_text(json.dumps(combined), encoding="utf-8")


def pipeline_message(stderr):
    """The pipeline scripts stop with one plain message; show it, but never a raw traceback."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines or any(line.startswith("Traceback") for line in lines):
        return "the pipeline hit an unexpected error. Check that the file is complete ScubaGear output."
    return lines[-1].removeprefix("ERROR: ")


def process_scan(name, raw):
    """Validate a scan file and run it through the pipeline. Returns (Assistant over the new findings,
    ScubaGear's result per policy).

    Raises ValueError with a message for the user when the file is rejected."""
    results = scan_results(check_scan(name, raw))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with tempfile.TemporaryDirectory(prefix="scuba-upload-") as tmp:
        tmp = Path(tmp)
        (tmp / "scuba-results.json").write_bytes(raw)
        # The builder links its outputs to its inputs by relative path, which fails on Windows when the
        # temp folder and the repo are on different drives, so every file it links to lives in tmp.
        combine_catalogs(CONTROLS_DIR, tmp / "catalog.json")
        steps = [
            ("convert the scan to OSCAL", [
                ROOT / "pipeline" / "make_assessment_results.py", "--results", tmp / "scuba-results.json",
                "--catalog", tmp / "catalog.json", "--out", tmp / "assessment-results.json",
                "--plan-out", tmp / "assessment-plan.json",
                "--allow-missing"]),  # controls the scan has no record for come out NOT_ASSESSED
            ("compare the scan with the SCuBA catalog", [
                ROOT / "comparison" / "compare_oscal.py", "--scuba", CONTROLS_DIR,
                "--scubagear", tmp / "assessment-results.json", "--output", tmp / "findings.json"]),
        ]
        for label, args in steps:
            try:
                r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=PIPELINE_TIMEOUT, cwd=ROOT, env=env)
            except subprocess.TimeoutExpired:
                raise ValueError(f"Couldn't {label}: it took longer than {PIPELINE_TIMEOUT} seconds.") from None
            if r.returncode != 0:
                log.error("Upload pipeline step failed (%s): %s", label, r.stderr)
                raise ValueError(f"Couldn't {label}: {pipeline_message(r.stderr)}")
        return Assistant(tmp / "findings.json"), results  # reads everything it needs now, so the folder can go


def reset_for_new_data():
    """Answers, notes and reports about the previous scan no longer apply."""
    st.session_state.messages = []
    st.session_state.notes = {}
    st.session_state.note_pending = None
    st.session_state.pending = None
    st.session_state.report = None
    st.session_state.report_error = None


def load_scan(name, raw):
    """Process a scan and, if it is accepted, make it this session's scan."""
    try:
        with st.spinner("Processing the scan…"):
            new_assistant, results = process_scan(name, raw)
    except ValueError as exc:
        st.session_state.upload_error = str(exc)
        return
    except Exception:
        log.exception("Could not process the scan")
        st.session_state.upload_error = "The scan couldn't be processed. Check that the file is ScubaGear output."
        return
    st.session_state.upload = {"name": name, "assistant": new_assistant, "scan_results": results}
    st.session_state.upload_error = None
    reset_for_new_data()
    st.session_state.flash = f"Loaded the scan for {new_assistant.info.get('tenant') or 'your tenant'}."
    st.rerun()  # switch from the upload panel to the dashboard


def handle_upload(uploaded):
    """Process each newly chosen file once. Removing a rejected file clears its error."""
    if uploaded is None:
        if st.session_state.upload_file_id is not None:
            st.session_state.upload_file_id = None
            st.session_state.upload_error = None
        return
    if uploaded.file_id == st.session_state.upload_file_id:
        return  # already handled on an earlier rerun (a rejected file stays in the box)
    st.session_state.upload_file_id = uploaded.file_id
    load_scan(uploaded.name, uploaded.getvalue())


# --- Compliance report (AI-written, delivered as a PDF) ------------------------
def format_time(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    except (TypeError, ValueError):
        return iso or "unknown"


def generate_report(assistant):
    """Have the AI write the report, then build the PDF. Keeps the result, or the error, in session state."""
    st.session_state.report_requested = False
    st.session_state.report_error = None
    try:
        reply = assistant.compliance_report()
    except Exception as exc:
        log.exception("Compliance report request failed")
        st.session_state.report_error = friendly_error(exc)
        return
    try:
        generated = datetime.now(timezone.utc)
        pdf = build_pdf(reply, assistant.findings, assistant.info, assistant.summary, generated)
    except Exception:
        log.exception("Could not build the report PDF")
        st.session_state.report_error = "The AI wrote the report, but the PDF could not be built. Try again."
        return
    st.session_state.report = {"pdf": pdf, "generated": generated,
                               "file_name": report_file_name(assistant.info.get("tenant"), generated)}


def report_file_name(tenant, generated):
    """<tenant>-scuba-compliance-report-<YYYY-MM-DD>-<HHMMSS>.pdf, time in UTC.

    The tenant name comes from the uploaded scan, so anything a file name can't hold (spaces,
    slashes, colons...) becomes a hyphen. The time has no colons, which Windows file names forbid."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", tenant or "").strip("-.") or "tenant"
    return f"{safe}-scuba-compliance-report-{generated:%Y-%m-%d}-{generated:%H%M%S}.pdf"


# --- Rendering ----------------------------------------------------------------
def result_badge(status, scuba_result=""):
    """(label, color, icon) for a control's result. The AI layer counts ScubaGear's Warning (a failed
    SHOULD) as FAIL; the page shows it as Warning, as ScubaGear and the PDF report do."""
    if status == "FAIL" and (scuba_result or "").lower() == "warning":
        return WARNING_BADGE
    return STATUS_BADGE.get(status, (status, "gray", None))


def status_badge(status, label=None, help=None, scuba_result=""):
    text, color, icon = result_badge(status, scuba_result)
    st.badge(label or text, color=color, icon=icon, help=help)


def goto(tab):
    st.session_state.tab = tab


def scan_age_days(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso).astimezone(timezone.utc)).days
    except (TypeError, ValueError):
        return None


def ai_off_note():
    """Says plainly what needs the AI and that everything else still works."""
    st.info(f"**AI features are off.** Questions, analyst notes and the PDF report need Azure OpenAI. {problem} "
            "Everything else works without it.", icon=":material/cloud_off:")


def styled_status(rows):
    """A dataframe with the Status column colour-coded like the badges."""
    colors = {"Pass": ("#2F7A58", "#E3EFE8"), "Fail": ("#B42318", "#F8E4E1"),
              "Warning": ("#B25E09", "#F9EBDB"), "Not assessed": ("#5F6B7A", "#ECEEEA")}
    return pd.DataFrame(rows).style.map(
        lambda v: f"color: {colors[v][0]}; background-color: {colors[v][1]}; font-weight: 600" if v in colors else "",
        subset=["Status"])


def requirement(text, small=False):
    """The baseline's own requirement text, quoted in the serif face."""
    css = "scb-req scb-req-sm" if small else "scb-req"
    st.html(f'<p class="{css}">{html.escape(text or "")}</p>')


def policy_number(control_id):
    """(section, policy) from MS.AAD.7.4v1 -> (7, 4); unknown ids sort last."""
    m = POLICY_ID.match(control_id)
    return (int(m[1]), int(m[2])) if m else (99, 0)


def open_control(control_id):
    st.session_state.open_control = control_id


def baseline_map(findings):
    """Every policy in the baseline, one row per SCuBA section, one cell per policy, coloured by result.
    Selecting a cell opens that control's details."""
    sections = {}
    for f in findings:
        section, _ = policy_number(f.control_id)
        sections.setdefault(section, {"title": f.group, "controls": []})["controls"].append(f)
    for section in sorted(sections):
        controls = sections[section]["controls"]
        passed = sum(f.status == "PASS" for f in controls)
        assessed = sum(f.status in ("PASS", "FAIL") for f in controls)
        with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
            with st.container(width=280, gap=None):
                st.markdown(f"**{section}**&nbsp;&nbsp;{sections[section]['title']}")
                st.caption(f"{passed} of {assessed} pass" if assessed else "Not assessed")
            with st.container(horizontal=True, gap="xxsmall", width="stretch"):
                for f in controls:
                    label = result_badge(f.status, f.scuba_result)[0]
                    sec, num = policy_number(f.control_id)
                    st.button(f"{sec}.{num}", key=f"map_{MAP_CLASS[label]}_{f.control_id.replace('.', '_')}",
                              help=f"{f.control_id}: {f.title} ({label})", on_click=open_control,
                              args=(f.control_id,))


def safe_key(control_id):
    return control_id.replace(".", "_")


def control_facts(f, key_prefix):
    """Everything verified about one control. For a failed control, the AI's analyst note sits in a
    narrower column beside the facts, so the two never mix."""
    if f.status == "FAIL":
        facts, margin = st.columns([3, 2], gap="large")
    else:
        facts, margin = st.container(), None
    with facts:
        label, _, _ = result_badge(f.status, f.scuba_result)
        with st.container(horizontal=True, gap="xsmall"):
            if f.priority in PRIORITY_BADGE:
                text, color = PRIORITY_BADGE[f.priority]
                st.badge(text, color=color)
            status_badge(f.status, label=f"ScubaGear: {label}" if f.status != "NOT ASSESSED" else None,
                         scuba_result=f.scuba_result)
            st.badge(f.obligation, color="gray")
            st.badge(f.group, color="gray")
        st.markdown("**Requirement**")
        requirement(f.requirement)
        if f.status == "NOT ASSESSED":
            st.markdown(f"**Why it has no result:** {not_assessed_reason(f.control_id, results_by_id)}")
        else:
            st.markdown(f"**Scan result:** {f.finding or 'No details recorded.'}")
        if f.evidence:
            st.caption(f"Evidence: {f.evidence}")
        if f.status == "FAIL" and f.remediation:
            st.markdown("**How to fix** (SCuBA guidance)")
            st.markdown(f.remediation)
        if f.nist:
            st.caption("Related NIST SP 800-53 controls: " + ", ".join(n.replace("NIST SP 800-53 Rev 5 ", "")
                                                                     for n in f.nist))
    if margin is not None:
        with margin:
            analyst_note(f, key_prefix)


def analyst_note(f, key_prefix):
    """The AI's short explanation of one failed control, written on request and kept for the session."""
    st.html('<p class="scb-note-label">Analyst note (AI)</p>')
    if st.session_state.note_pending == f.control_id and not problem:
        with st.spinner("Writing the note…"):
            write_note(f)
    note = st.session_state.notes.get(f.control_id)
    if note is None:
        st.caption("A short AI explanation of why this failure matters and how to approach the fix. "
                   "Check it against the facts beside it.")
        st.button("Write analyst note", key=f"{key_prefix}_note_{safe_key(f.control_id)}", disabled=bool(problem),
                  on_click=request_note, args=(f.control_id, key_prefix == "dialog"))
    elif note.get("error"):
        st.error(note["error"], icon=":material/error:")
        st.button("Try again", key=f"{key_prefix}_note_{safe_key(f.control_id)}", disabled=bool(problem),
                  on_click=request_note, args=(f.control_id, key_prefix == "dialog"))
    else:
        with st.container(key=f"note_{key_prefix}_{safe_key(f.control_id)}"):
            st.markdown(note["text"])
            if note["unverified"]:
                st.caption("Mentions controls outside this assessment, so not verified: "
                           + ", ".join(note["unverified"]))


def write_note(f):
    st.session_state.note_pending = None
    try:
        reply = assistant.answer(NOTE_REQUEST.format(control_id=f.control_id))
        st.session_state.notes[f.control_id] = {"text": reply["text"], "unverified": reply["unverified_references"]}
    except Exception as exc:
        log.exception("Analyst note failed")
        st.session_state.notes[f.control_id] = {"error": friendly_error(exc)}


@st.dialog("Control details", width="large")
def control_dialog(f):
    st.html(f'<p class="scb-dialog-title"><b>{html.escape(f.control_id)}</b>&nbsp;&nbsp;{html.escape(f.title)}</p>')
    control_facts(f, "dialog")


def worklist_row(f, key_prefix):
    """One failed control as a flat row with a result bar on the left; opens to the full details."""
    label, color, _ = result_badge(f.status, f.scuba_result)
    with st.expander(f"**{f.control_id}**&nbsp;&nbsp;{f.title}&nbsp;&nbsp;:{color}[{label}]",
                     key=f"row_{MAP_CLASS[label]}_{key_prefix}_{safe_key(f.control_id)}"):
        control_facts(f, key_prefix)


def render_exchange(question, answer, index):
    """One question and its answer in the Q&A record, with the controls it cites listed underneath."""
    if index:
        st.html('<hr class="scb-rule">')
    st.html(f'<p class="scb-q">{html.escape(question)}</p>')
    if answer is None:
        return
    if answer.get("error"):
        st.error(answer["content"], icon=":material/error:")
        return
    st.caption("AI analysis")
    st.markdown(answer["content"])
    if answer["unverified"]:
        st.warning("Mentioned but not part of this assessment, so not verified: "
                   + ", ".join(f"`{c}`" for c in answer["unverified"]), icon=":material/report:")
    if answer["verified"]:
        st.caption("Controls this answer relies on (select one to see the verified facts)")
        for i, fact in enumerate(answer["verified"], 1):
            label, color, _ = result_badge(fact["status"], fact.get("scuba_result"))
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(f"{i}.", width=22)
                st.button(f"{fact['control_id']}  {fact['title']}", key=f"cite_{index}_{i}", type="tertiary",
                          on_click=open_control, args=(fact["control_id"],))
                st.markdown(f":{color}[{label}]", width="content")


# --- State and callbacks ------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending" not in st.session_state:
    st.session_state.pending = None
if "report" not in st.session_state:
    st.session_state.report = None             # {"pdf", "generated", "file_name"} once generated
    st.session_state.report_requested = False
    st.session_state.report_error = None
if "notes" not in st.session_state:
    st.session_state.notes = {}                # control id -> analyst note (or error) for this scan
    st.session_state.note_pending = None
if "upload" not in st.session_state:
    st.session_state.upload = None             # {"name", "assistant"} once a scan is loaded
    st.session_state.upload_error = None
    st.session_state.upload_file_id = None
    st.session_state.load_sample = False
    st.session_state.flash = None              # toast to show after switching to the dashboard


def queue(kind, prompt, control_id=None):
    """Send a request to the AI on the next run, and show the chat so the answer is visible."""
    st.session_state.pending = {"kind": kind, "prompt": prompt, "control_id": control_id}
    goto(TAB_CHAT)


def request_note(control_id, reopen_dialog=False):
    st.session_state.note_pending = control_id
    if reopen_dialog:   # a click inside the dialog reruns the page; keep the dialog open for the note
        open_control(control_id)


def clear_conversation():
    st.session_state.messages = []
    st.session_state.pending = None


def request_report():
    st.session_state.report_requested = True


def request_sample():
    st.session_state.load_sample = True


def clear_scan():
    """Back to the upload panel."""
    st.session_state.upload = None
    st.session_state.upload_error = None
    st.session_state.upload_file_id = None
    reset_for_new_data()


# --- Page ---------------------------------------------------------------------
loaded = st.session_state.upload is not None
st.set_page_config(page_title="SCuBA posture assistant", page_icon=PAGE_ICON,
                   layout="wide" if loaded else "centered",           # the dashboard uses the full width
                   initial_sidebar_state="expanded" if loaded else "collapsed")

problem = ai_problem()
st.html(STYLE)

with st.sidebar:
    st.markdown("**SCuBA posture assistant**")
    st.caption("Checks a Microsoft Entra ID tenant against the CISA SCuBA baseline.")
    if problem:
        st.badge("AI offline", color="red")
        st.caption("Questions, analyst notes and the PDF report are unavailable.")


# --- Upload panel: shown until a scan is loaded --------------------------------
if st.session_state.upload is None:
    st.title("SCuBA posture assistant", anchor=False)
    st.caption("Upload the results of a ScubaGear scan to see where your Microsoft Entra ID tenant stands "
               "against the CISA SCuBA baseline, ask questions about it, and generate a compliance report.")
    with st.container(border=True):
        st.subheader("Upload your ScubaGear results", anchor=False)
        st.markdown("Choose the **ScubaResults JSON file** from your ScubaGear output folder. "
                    f"Only .json files are accepted, up to {MAX_UPLOAD_MB} MB.")
        uploaded = st.file_uploader("ScubaGear results file", type=["json"], key="scan_upload",
                                    max_upload_size=MAX_UPLOAD_MB, label_visibility="collapsed")
        handle_upload(uploaded)
        if st.session_state.load_sample:
            st.session_state.load_sample = False
            load_scan(SAMPLE_SCAN.name, SAMPLE_SCAN.read_bytes())
        if st.session_state.upload_error:
            st.error(st.session_state.upload_error, icon=":material/block:")
        with st.container(horizontal=True, vertical_alignment="center"):
            st.caption("No scan to hand?", width="content")
            st.button("Try the sample scan", type="tertiary", on_click=request_sample)
        with st.expander("Where do I find this file?"):
            st.markdown(
                "1. Run ScubaGear in PowerShell, for example `Invoke-SCuBA -ProductNames aad`.\n"
                "2. Open the output folder it creates, named like `M365BaselineConformance_<date>`.\n"
                "3. Upload the **ScubaResults** JSON file from that folder (`ScubaResults.json`, or "
                "`ScubaResults_<id>.json` in newer versions).")

    st.markdown(
        "**What you'll get**\n\n"
        "- **Overview:** every policy in the baseline at a glance, and what to fix first\n"
        "- **Findings:** each failed control with what the scan found and how to fix it\n"
        "- **Questions:** ask about the scan; answers list the controls they rely on\n"
        "- **Report:** a PDF compliance report to share")
    if problem:
        ai_off_note()
    st.caption("Your file is processed in memory on this server and never saved. Only you can see it, and it is "
               "gone when you refresh or close the page. When you use questions, analyst notes or the report, the "
               "scan's per-control results are sent to Azure OpenAI.")
    st.stop()

# --- Dashboard: a scan is loaded -------------------------------------------------
assistant = st.session_state.upload["assistant"]
findings, info, summary = assistant.findings, assistant.info, assistant.summary
failures = prioritized_failures(findings)
high_priority = [f for f in failures if f.priority == "high"]
unassessed = [f for f in findings if f.status == "NOT ASSESSED"]
results_by_id = st.session_state.upload.get("scan_results", {})
age = scan_age_days(info.get("scan_time"))
scanned = format_time(info.get("scan_time"))
if st.session_state.flash:
    st.toast(st.session_state.flash, icon=":material/check_circle:")
    st.session_state.flash = None

with st.sidebar:
    st.space("small")
    st.markdown("**Scan**")
    age_text = f"<br><span>{age} day{'s' if age != 1 else ''} ago</span>" if age is not None else ""
    st.html('<dl class="scb-dl">'
            f"<dt>Tenant</dt><dd>{html.escape(info.get('tenant') or 'Unknown')}</dd>"
            f"<dt>Domain</dt><dd>{html.escape(info.get('domain') or 'Unknown')}</dd>"
            f"<dt>Scanned</dt><dd>{html.escape(scanned)}{age_text}</dd>"
            f"<dt>Scanner</dt><dd>ScubaGear {html.escape(info.get('tool_version') or 'unknown')}</dd>"
            f"<dt>File</dt><dd>{html.escape(st.session_state.upload['name'])}</dd></dl>")
    st.space("small")
    st.button("Upload a different scan", width="stretch", on_click=clear_scan)

# The tenant is the subject of the page, so it is the title.
st.title(info.get("domain") or info.get("tenant") or "Your tenant", anchor=False)
st.caption(f"Microsoft Entra ID assessed against the CISA SCuBA baseline, from a ScubaGear "
           f"{info.get('tool_version') or ''} scan on {scanned}. Statuses come from the scan; "
           "anything the AI writes is labelled as AI.")
if age is not None and age > STALE_SCAN_DAYS:
    st.warning(f"**This scan is {age} days old.** The tenant's settings may have changed since; re-run "
               "ScubaGear for an up-to-date picture.", icon=":material/history:")
if problem:
    ai_off_note()

tab_overview, tab_findings, tab_chat, tab_report = st.tabs(
    [TAB_OVERVIEW, TAB_FINDINGS, TAB_CHAT, TAB_REPORT], key="tab", on_change="rerun")

# Overview: the verdict, the whole baseline at a glance, and what to fix first
with tab_overview:
    if failures:
        verdict = (f"{summary['failed']} of {summary['assessed']} assessed controls fail. "
                   f"{len(high_priority)} of them {'is' if len(high_priority) == 1 else 'are'} required (SHALL).")
    else:
        verdict = f"All {summary['assessed']} assessed controls pass."
    st.html(f'<p class="scb-verdict">{html.escape(verdict)}</p>')
    counts = {label: sum(result_badge(f.status, f.scuba_result)[0] == label for f in findings) for label in MAP_CLASS}
    st.html('<div class="scb-legend">' + "".join(
        f'<span><i class="scb-sw-{MAP_CLASS[label]}"></i>{label} {n}</span>' for label, n in counts.items())
        + "</div>")
    baseline_map(findings)
    st.caption("Select a policy to see its requirement, what the scan found and how to fix it.")

    st.subheader("Fix first", anchor=False)
    if failures:
        top = high_priority or failures
        st.caption("The highest-priority failures: required (SHALL) controls that the scan found not met."
                   if high_priority else "No required control failed; these recommended (SHOULD) controls did.")
        for f in top[:FIX_FIRST]:
            worklist_row(f, "overview")
        st.button(f"See all {len(failures)} findings", type="tertiary", on_click=goto, args=(TAB_FINDINGS,))
    else:
        st.success("Every assessed control passed.", icon=":material/verified:")

    if unassessed:
        reasons = [not_assessed_reason(f.control_id, results_by_id) for f in unassessed]
        cant_check = sum(results_by_id.get(f.control_id) == "N/A" for f in unassessed)
        missing = sum(f.control_id not in results_by_id for f in unassessed)
        parts = [f"{cant_check} can't be checked by ScubaGear automatically" if cant_check else "",
                 f"{missing} {'is' if missing == 1 else 'are'} newer than ScubaGear "
                 f"{info.get('tool_version') or ''}".rstrip() if missing else ""]
        st.subheader(f"Not assessed ({len(unassessed)})", anchor=False)
        st.caption("ScubaGear returned no pass or fail for these controls, so their status is unknown"
                   + (": " + " and ".join(p for p in parts if p) if any(parts) else "") + ". Check them by hand.")
        st.dataframe(
            [{"Control": f.control_id, "Title": f.title, "Obligation": f.obligation, "Why": why}
             for f, why in zip(unassessed, reasons)],
            hide_index=True,
            column_config={"Control": st.column_config.TextColumn(width=115),
                           "Title": st.column_config.TextColumn(width="medium"),
                           "Obligation": st.column_config.TextColumn(width=95),
                           "Why": st.column_config.TextColumn(width="large")})

# Findings: every failed control as a worklist, filterable
with tab_findings:
    if not failures:
        st.success("Every assessed control passed.", icon=":material/verified:")
    else:
        with st.container(horizontal=True, vertical_alignment="bottom", gap="medium"):
            priority = st.segmented_control("Priority", ["All", "High", "Moderate"], default="All", required=True,
                                            key="filter_priority")
            area = st.selectbox("Area", ["All areas", *sorted({f.group for f in failures})], key="filter_area",
                                width=320)
        shown = [f for f in failures if priority in (None, "All") or f.priority == priority.lower()]
        shown = [f for f in shown if area == "All areas" or f.group == area]
        st.caption(f"Showing {len(shown)} of {len(failures)} failed controls. Open one to see what the scan found "
                   "and how to fix it.")
        for level, title, explain in (("high", "High priority", "failed SHALL and SHALL NOT requirements"),
                                      ("moderate", "Moderate priority", "ScubaGear warnings: failed SHOULD requirements")):
            group = [f for f in shown if f.priority == level]
            if group:
                st.markdown(f"**{title} ({len(group)})**: {explain}")
                for f in group:
                    worklist_row(f, "findings")

    st.subheader("All controls", anchor=False)
    with st.container(horizontal=True, vertical_alignment="bottom", gap="medium"):
        query = st.text_input("Search", placeholder="Control ID, title or area", type="search", live=True,
                              icon=":material/search:", key="controls_search", width=320)
        picked = st.pills("Status", ["Pass", "Fail", "Warning", "Not assessed"], selection_mode="multi",
                          key="controls_status")
    rows = [{"Control": f.control_id, "Title": f.title, "Area": f.group, "Obligation": f.obligation,
             "Status": result_badge(f.status, f.scuba_result)[0]} for f in findings]
    q = (query or "").strip().lower()
    rows = [r for r in rows if (not q or q in f"{r['Control']} {r['Title']} {r['Area']}".lower())
            and (not picked or r["Status"] in picked)]
    st.caption(f"Showing {len(rows)} of {len(findings)} controls.")
    if rows:
        st.dataframe(styled_status(rows), hide_index=True,
                     column_config={"Control": st.column_config.TextColumn(width=115),
                                    "Title": st.column_config.TextColumn(width="large"),
                                    "Area": st.column_config.TextColumn(width="medium"),
                                    "Obligation": st.column_config.TextColumn(width=95),
                                    "Status": st.column_config.TextColumn(width=110)})

# Questions: a record of questions and answers, newest first
with tab_chat:
    typed = st.chat_input("AI is offline, see the note above" if problem else "Ask about this scan",
                          submit_mode="disable", disabled=bool(problem))
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption("Answers are written by the AI from the verified results, and list the controls they rely on.",
                   width="stretch")
        st.button("Executive summary", disabled=bool(problem), on_click=queue,
                  args=("summary", "Give me an executive summary of our Entra ID security posture."))
        st.button("Clear questions", type="tertiary", on_click=clear_conversation,
                  disabled=not st.session_state.messages)

    request = {"kind": "chat", "prompt": typed, "control_id": None} if typed else st.session_state.pending
    st.session_state.pending = None
    if request:
        history = chat_history()
        user_msg = {"role": "user", "content": request["prompt"]}
        try:
            with st.spinner("Working on the answer…"):
                reply = ask(assistant, request, history)
            msg = {"role": "assistant", "content": reply["text"], "verified": reply["verified"],
                   "unverified": reply["unverified_references"]}
        except Exception as exc:
            log.exception("AI request failed")
            user_msg["failed"] = True
            msg = {"role": "assistant", "content": friendly_error(exc), "error": True}
        st.session_state.messages += [user_msg, msg]

    msgs = st.session_state.messages
    exchanges = [(msgs[i]["content"], msgs[i + 1] if i + 1 < len(msgs) else None) for i in range(0, len(msgs), 2)]
    if not exchanges:
        st.markdown("Ask anything about this scan. For example:")
        for q in EXAMPLE_QUESTIONS:
            st.button(q, key=f"example_{q}", type="tertiary", disabled=bool(problem), on_click=queue, args=("chat", q))
    for n, (question, answer) in enumerate(reversed(exchanges)):
        render_exchange(question, answer, n)

# Report: the AI-written PDF
with tab_report:
    st.subheader("Compliance report", anchor=False)
    st.markdown("A PDF for security and compliance stakeholders, built from this scan:")
    st.markdown(
        "- **Cover:** tenant, scan details and the pass and fail counts\n"
        "- **Executive summary** and **recommended next steps** (AI analysis)\n"
        "- **Findings:** one card per failed control with the verified requirement and scan result, plus the "
        "AI's explanation and fix steps\n"
        "- **Controls that passed**, **not assessed**, and an **appendix** of every control (verified results)")
    report_slot = st.container()   # filled at the end of the script, so the page renders before the slow AI call

with report_slot:
    if st.session_state.report_requested and not problem:
        with st.spinner("The AI is writing the report. This can take a minute…"):
            generate_report(assistant)
    report = st.session_state.report
    if report:
        st.download_button("Download PDF", data=report["pdf"], file_name=report["file_name"],
                           mime="application/pdf", icon=":material/download:", type="primary", on_click="ignore")
        st.caption(f"{report['file_name']}, generated {report['generated']:%d %b %Y, %H:%M UTC}. "
                   "AI-written; review it before sharing.")
    if st.session_state.report_error:
        st.error(st.session_state.report_error, icon=":material/error:")
    if problem:
        st.caption("Generating a report needs the AI; see the note above.")
    st.button("Regenerate report" if report else "Generate PDF report",
              type="secondary" if report else "primary", disabled=bool(problem), on_click=request_report,
              help="The AI writes the analysis; the facts come straight from the scan.")

# A policy selected on the map or in an answer opens its details over the page.
if st.session_state.get("open_control"):
    chosen = next((f for f in findings if f.control_id == st.session_state.open_control), None)
    st.session_state.open_control = None
    if chosen:
        control_dialog(chosen)
