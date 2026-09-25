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

ASSISTANT_AVATAR = ":material/shield_lock:"
USER_AVATAR = ":material/person:"

STATUS_BADGE = {  # status -> (label, color, icon)
    "PASS": ("Pass", "green", ":material/check_circle:"),
    "FAIL": ("Fail", "red", ":material/cancel:"),
    "NOT ASSESSED": ("Not assessed", "gray", ":material/help:"),
}
WARNING_BADGE = ("Warning", "orange", ":material/warning:")   # ScubaGear's word for a failed SHOULD
PRIORITY_BADGE = {"high": ("High priority", "red"), "moderate": ("Moderate priority", "orange")}

TAB_OVERVIEW = ":material/dashboard: Overview"
TAB_FINDINGS = ":material/fact_check: Findings"
TAB_CHAT = ":material/forum: Ask the AI"
TAB_REPORT = ":material/picture_as_pdf: Report"
FIX_FIRST = 3   # high-priority failures shown on the overview

SUGGESTIONS = {
    ":material/low_priority: What should we fix first?": "What should we fix first, and why?",
    ":material/gpp_maybe: Our biggest identity risk": "What is our biggest identity risk right now?",
    ":material/admin_panel_settings: Privileged access": "How are we doing on privileged access?",
    ":material/passkey: MFA and sign-in": "How are we doing on MFA and sign-in protection?",
}

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
    """Answers and reports about the previous scan no longer apply."""
    st.session_state.messages = []
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
    st.info(f"**AI features are off.** The chat, Ask AI and the PDF report need Azure OpenAI. {problem} "
            "Everything else works without it.", icon=":material/cloud_off:")


def styled_status(rows):
    """A dataframe with the Status column colour-coded like the badges."""
    colors = {"Pass": ("#1F7A5A", "#E4F4EC"), "Fail": ("#B3261E", "#FBEAE8"),
              "Warning": ("#9A5A00", "#FDF3E0"), "Not assessed": ("#5B6A80", "#ECEFF4")}
    return pd.DataFrame(rows).style.map(
        lambda v: f"color: {colors[v][0]}; background-color: {colors[v][1]}; font-weight: 600" if v in colors else "",
        subset=["Status"])


def ask_ai_button(f, key_prefix):
    st.button("Ask AI", key=f"{key_prefix}_{f.control_id}", icon=":material/auto_awesome:", type="tertiary",
              disabled=bool(problem), on_click=queue,
              args=("explain", f"Explain `{f.control_id}`: what it requires, what the scan found, and how to "
                               "fix it.", f.control_id))


def area_rows(findings):
    """One row per catalog area: pass rate over the assessed controls, and the counts behind it.
    Areas with the most failures come first."""
    areas = {}
    for f in findings:
        counts = areas.setdefault(f.group or "Other", {"PASS": 0, "FAIL": 0, "NOT ASSESSED": 0})
        counts[f.status] = counts.get(f.status, 0) + 1
    rows = []
    for area, c in areas.items():
        assessed = c["PASS"] + c["FAIL"]
        rows.append({"Area": area, "Pass rate": round(100 * c["PASS"] / assessed) if assessed else None,
                     "Passing": c["PASS"], "Failing": c["FAIL"], "Not assessed": c["NOT ASSESSED"]})
    return sorted(rows, key=lambda r: -r["Failing"])


def finding_card(f):
    """One failed control on the overview: title, tags, requirement and an Ask AI button."""
    with st.container(border=True):
        st.markdown(f"**{f.control_id}** · {f.title}")
        st.caption(f.requirement)
        with st.container(horizontal=True, vertical_alignment="center", horizontal_alignment="distribute"):
            with st.container(horizontal=True, gap="xsmall", width="content"):
                text, color = PRIORITY_BADGE[f.priority]
                st.badge(text, color=color)
                status_badge(f.status, scuba_result=f.scuba_result)
            ask_ai_button(f, "overview")


def finding_details(f):
    """One failed control on the Findings tab, with every verified fact behind it."""
    icon = ":material/error:" if f.priority == "high" else ":material/warning:"
    with st.expander(f"**{f.control_id}** · {f.title}", icon=icon):
        with st.container(horizontal=True, gap="xsmall"):
            text, color = PRIORITY_BADGE[f.priority]
            st.badge(text, color=color)
            label, _, _ = result_badge(f.status, f.scuba_result)
            status_badge(f.status, label=f"ScubaGear: {label}", scuba_result=f.scuba_result)
            st.badge(f.obligation, color="gray")
            st.badge(f.group, color="gray", icon=":material/category:")
        st.markdown(f"**Requirement:** {f.requirement}")
        st.markdown(f"**Scan result:** {f.finding or 'No details recorded.'}")
        if f.evidence:
            st.caption(f":material/description: {f.evidence}")
        if f.remediation:
            st.markdown("**How to fix** (SCuBA guidance)")
            st.markdown(f.remediation)
        if f.nist:
            st.caption("Related NIST SP 800-53 controls: " + ", ".join(n.replace("NIST SP 800-53 Rev 5 ", "")
                                                                     for n in f.nist))
        ask_ai_button(f, "findings")


def render_fact(f):
    """One verified record, straight from OSCAL."""
    st.markdown(f"**{f['control_id']}** · {f['title']}")
    with st.container(horizontal=True, gap="xsmall"):
        status_badge(f["status"], scuba_result=f.get("scuba_result"))
        st.badge(f["obligation"], color="gray")
        if f.get("priority") in PRIORITY_BADGE:
            text, color = PRIORITY_BADGE[f["priority"]]
            st.badge(text, color=color)
    st.markdown(f"**Requirement:** {f['requirement']}")
    if f.get("finding"):
        st.markdown(f"**Scan result:** {f['finding']}")
    if f.get("evidence"):
        st.caption(f":material/description: {f['evidence']}")
    if f["status"] == "FAIL" and f.get("remediation"):
        st.markdown("**Remediation (SCuBA guidance)**")
        st.markdown(f["remediation"])


def render_reply(msg):
    if msg.get("error"):
        st.error(msg["content"], icon=":material/error:")
        return
    st.caption(":material/auto_awesome: AI-generated analysis. Check it against the verified facts below.")
    st.markdown(msg["content"])
    if msg["unverified"]:
        ids = ", ".join(f"`{c}`" for c in msg["unverified"])
        st.warning(f"Mentioned but not part of this assessment, so not verified: {ids}", icon=":material/report:")
    if msg["verified"]:
        with st.container(horizontal=True, gap="xsmall"):
            for f in msg["verified"]:
                result = result_badge(f["status"], f.get("scuba_result"))[0].lower()
                status_badge(f["status"], label=f["control_id"], help=f"{f['title']}: {result}",
                             scuba_result=f.get("scuba_result"))
        n = len(msg["verified"])
        with st.expander(f"Verified facts from OSCAL ({n} control{'s' if n != 1 else ''})", icon=":material/verified:"):
            for i, f in enumerate(msg["verified"]):
                if i:
                    st.space("small")
                render_fact(f)


def render_message(msg):
    if msg["role"] == "user":
        with st.chat_message("user", avatar=USER_AVATAR):
            st.markdown(msg["content"])
    else:
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            render_reply(msg)


# --- State and callbacks ------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending" not in st.session_state:
    st.session_state.pending = None
if "report" not in st.session_state:
    st.session_state.report = None             # {"pdf", "generated", "file_name"} once generated
    st.session_state.report_requested = False
    st.session_state.report_error = None
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


def pick_suggestion():
    choice = st.session_state.suggestion
    if choice:
        queue("chat", SUGGESTIONS[choice])
    st.session_state.suggestion = None


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
st.set_page_config(page_title="SCuBA posture assistant", page_icon=ASSISTANT_AVATAR,
                   layout="wide" if loaded else "centered",           # the dashboard uses the full width
                   initial_sidebar_state="expanded" if loaded else "collapsed")

problem = ai_problem()

with st.sidebar:
    st.markdown(f"### {ASSISTANT_AVATAR} SCuBA assistant")
    st.caption("CISA SCuBA baseline for Microsoft Entra ID (MS.AAD)")
    if problem:
        st.badge("AI offline", icon=":material/cloud_off:", color="red")
        st.caption("The chat, Ask AI and the PDF report are unavailable.")
    else:
        st.badge("AI ready", icon=":material/cloud_done:", color="green")


# --- Upload panel: shown until a scan is loaded --------------------------------
if st.session_state.upload is None:
    st.title("SCuBA posture assistant", icon=ASSISTANT_AVATAR)
    st.caption("Upload the results of a ScubaGear scan to see where your Microsoft Entra ID tenant stands "
               "against the CISA SCuBA baseline, ask questions about it, and generate a compliance report.")
    with st.container(border=True):
        st.subheader("Upload your ScubaGear results", icon=":material/upload_file:")
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
            st.button("Try the sample scan", icon=":material/science:", type="tertiary", on_click=request_sample)
        with st.expander("Where do I find this file?", icon=":material/help:"):
            st.markdown(
                "1. Run ScubaGear in PowerShell, for example `Invoke-SCuBA -ProductNames aad`.\n"
                "2. Open the output folder it creates, named like `M365BaselineConformance_<date>`.\n"
                "3. Upload the **ScubaResults** JSON file from that folder (`ScubaResults.json`, or "
                "`ScubaResults_<id>.json` in newer versions).")

    st.markdown("**What you'll get**")
    with st.container(horizontal=True, gap="small"):
        for icon, title, text in (
                (":material/dashboard:", "Overview", "Pass rates by area and what to fix first"),
                (":material/fact_check:", "Findings", "Every failed control with the scan result and fix steps"),
                (":material/forum:", "Ask the AI", "Questions answered with the verified results cited"),
                (":material/picture_as_pdf:", "Report", "A PDF compliance report to share")):
            with st.container(border=True):
                st.markdown(f"{icon} **{title}**")
                st.caption(text)
    if problem:
        ai_off_note()
    st.caption(":material/lock: Your file is processed in memory on this server and never saved. Only you "
               "can see it, and it is gone when you refresh or close the page. When you use the chat or the "
               "report, the scan's per-control results are sent to Azure OpenAI.")
    st.stop()

# --- Dashboard: a scan is loaded -------------------------------------------------
assistant = st.session_state.upload["assistant"]
findings, info, summary = assistant.findings, assistant.info, assistant.summary
failures = prioritized_failures(findings)
high_priority = [f for f in failures if f.priority == "high"]
warnings = sum(result_badge(f.status, f.scuba_result) == WARNING_BADGE for f in failures)
unassessed = [f for f in findings if f.status == "NOT ASSESSED"]
results_by_id = st.session_state.upload.get("scan_results", {})
age = scan_age_days(info.get("scan_time"))
if st.session_state.flash:
    st.toast(st.session_state.flash, icon=":material/check_circle:")
    st.session_state.flash = None

with st.sidebar:
    st.space("small")
    st.markdown("**Scan**")
    st.caption(f":material/upload_file: {st.session_state.upload['name']}")
    st.markdown(
        f":material/domain: {info.get('tenant') or 'Unknown tenant'}  \n"
        f":material/language: {info.get('domain') or 'Unknown domain'}  \n"
        f":material/schedule: {format_time(info.get('scan_time'))}"
        + (f" ({age} day{'s' if age != 1 else ''} ago)" if age is not None else "") + "  \n"
        f":material/build: ScubaGear {info.get('tool_version') or 'unknown'}"
    )
    st.button("Upload a different scan", icon=":material/upload:", width="stretch", on_click=clear_scan)

st.title("SCuBA posture assistant", icon=ASSISTANT_AVATAR)
st.caption(f"ScubaGear scan of {info.get('tenant') or 'your tenant'}, checked against the CISA SCuBA baseline for "
           "Microsoft Entra ID. Statuses come from the scan; anything the AI writes is labelled as AI analysis.")
if age is not None and age > STALE_SCAN_DAYS:
    st.warning(f"**This scan is {age} days old** (run {format_time(info.get('scan_time'))}). The tenant's settings "
               "may have changed since; re-run ScubaGear for an up-to-date picture.", icon=":material/history:")
if problem:
    ai_off_note()

tab_overview, tab_findings, tab_chat, tab_report = st.tabs(
    [TAB_OVERVIEW, TAB_FINDINGS, TAB_CHAT, TAB_REPORT], key="tab", on_change="rerun")

# Overview: the headline numbers, where the problems are, and what to fix first
with tab_overview:
    with st.container(horizontal=True):
        st.metric("Passing", f"{summary['passed']} of {summary['assessed']}", icon=":material/check_circle:",
                  border=True, help="Assessed controls that meet the SCuBA requirement")
        st.metric("Failing", summary["failed"], icon=":material/cancel:", border=True,
                  help=f"{summary['failed'] - warnings} failed SHALL / SHALL NOT requirements and {warnings} "
                       "ScubaGear warnings (failed SHOULD requirements)")
        st.metric("High priority", len(high_priority), icon=":material/priority_high:", border=True,
                  help="Failed SHALL and SHALL NOT requirements")
        st.metric("Not assessed", summary["not_assessed"], icon=":material/help:", border=True,
                  help="Controls ScubaGear returned no result for")

    st.subheader("Fix first", icon=":material/low_priority:")
    if failures:
        top = high_priority or failures
        st.caption("The highest-priority failures: required (SHALL) controls that the scan found not met."
                   if high_priority else "No required control failed; these recommended (SHOULD) controls did.")
        with st.container(horizontal=True, gap="medium"):
            for f in top[:FIX_FIRST]:
                finding_card(f)
        st.button(f"See all {len(failures)} findings", icon=":material/arrow_forward:", type="tertiary",
                  on_click=goto, args=(TAB_FINDINGS,))
    else:
        st.success("Every assessed control passed.", icon=":material/verified:")

    st.subheader("Posture by area", icon=":material/category:")
    st.caption("Pass rate counts only the controls ScubaGear assessed. Areas with the most failures come first.")
    st.dataframe(
        area_rows(findings), hide_index=True,
        column_config={
            "Area": st.column_config.TextColumn(width="large"),
            "Pass rate": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100, width="medium"),
            "Passing": st.column_config.NumberColumn(width="small"),
            "Failing": st.column_config.NumberColumn(width="small"),
            "Not assessed": st.column_config.NumberColumn(width="small"),
        })

    if unassessed:
        reasons = [not_assessed_reason(f.control_id, results_by_id) for f in unassessed]
        cant_check = sum(results_by_id.get(f.control_id) == "N/A" for f in unassessed)
        missing = sum(f.control_id not in results_by_id for f in unassessed)
        parts = [f"{cant_check} can't be checked by ScubaGear automatically" if cant_check else "",
                 f"{missing} {'is' if missing == 1 else 'are'} newer than ScubaGear "
                 f"{info.get('tool_version') or ''}".rstrip() if missing else ""]
        st.subheader(f"Not assessed ({len(unassessed)})", icon=":material/help:")
        st.caption("ScubaGear returned no pass or fail for these controls, so their status is unknown"
                   + (": " + " and ".join(p for p in parts if p) if any(parts) else "") + ". Check them by hand.")
        st.dataframe(
            [{"Control": f.control_id, "Title": f.title, "Obligation": f.obligation, "Why": why}
             for f, why in zip(unassessed, reasons)],
            hide_index=True,
            column_config={"Control": st.column_config.TextColumn(width=115),
                           "Obligation": st.column_config.TextColumn(width=95),
                           "Why": st.column_config.TextColumn(width="large")})

# Findings: every failed control with its verified facts, filterable
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
        for level, title in (("high", "High priority"), ("moderate", "Moderate priority")):
            group = [f for f in shown if f.priority == level]
            if group:
                st.markdown(f"**{title}** ({len(group)})")
                for f in group:
                    finding_details(f)

    st.subheader("All controls", icon=":material/table_rows:")
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

# Ask the AI: the chat
with tab_chat:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(":material/auto_awesome: Answers are AI-generated and cite the verified results they rely on.",
                   width="stretch")
        st.button("Executive summary", icon=":material/summarize:", disabled=bool(problem), on_click=queue,
                  args=("summary", "Give me an executive summary of our Entra ID security posture."))
        st.button("New conversation", icon=":material/restart_alt:", type="tertiary", on_click=clear_conversation,
                  disabled=not st.session_state.messages)
    chat_box = st.container()   # the conversation, above the input box
    typed = st.chat_input("AI is offline, see the sidebar" if problem else "Ask about your Entra ID security posture",
                          submit_mode="disable", disabled=bool(problem))
    request = {"kind": "chat", "prompt": typed, "control_id": None} if typed else st.session_state.pending
    st.session_state.pending = None

    with chat_box:
        for msg in st.session_state.messages:
            render_message(msg)

        if not st.session_state.messages and not request:
            with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
                st.markdown("I answer questions about this tenant's SCuBA scan and point to the verified "
                            "results behind every answer. Type a question below, or start with one of these:")
                st.pills("Suggested questions", list(SUGGESTIONS), key="suggestion", on_change=pick_suggestion,
                         label_visibility="collapsed", disabled=bool(problem))

        if request:
            history = chat_history()
            user_msg = {"role": "user", "content": request["prompt"]}
            st.session_state.messages.append(user_msg)
            render_message(user_msg)

            with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
                try:
                    with st.spinner("Analyzing your assessment…"):
                        reply = ask(assistant, request, history)
                    msg = {"role": "assistant", "content": reply["text"], "verified": reply["verified"],
                           "unverified": reply["unverified_references"]}
                except Exception as exc:
                    log.exception("AI request failed")
                    user_msg["failed"] = True
                    msg = {"role": "assistant", "content": friendly_error(exc), "error": True}
                render_reply(msg)
            st.session_state.messages.append(msg)

# Report: the AI-written PDF
with tab_report:
    st.subheader("Compliance report", icon=":material/picture_as_pdf:")
    st.markdown("A PDF for security and compliance stakeholders, built from this scan:")
    st.markdown(
        "- **Cover:** tenant, scan details and the pass / fail counts\n"
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
        st.caption(f"{report['file_name']} · generated {report['generated']:%d %b %Y, %H:%M UTC}. "
                   "AI-written; review it before sharing.")
    if st.session_state.report_error:
        st.error(st.session_state.report_error, icon=":material/error:")
    if problem:
        st.caption("Generating a report needs the AI; see the sidebar.")
    st.button("Regenerate report" if report else "Generate PDF report", icon=":material/picture_as_pdf:",
              type="secondary" if report else "primary", disabled=bool(problem), on_click=request_report,
              help="The AI writes the analysis; the facts come straight from the scan.")
