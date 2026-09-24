"""SCuBA posture assistant: the chat frontend for the AI layer in ai/.

Run from the repo root:  streamlit run app/app.py

Everything typed in the chat goes to ai.answer(question, history). Each reply shows the AI's
analysis, labelled as AI output, next to the verified OSCAL facts for every control it cites.
The scan summary and the downloadable report come from the same verified records the AI reads
(ai/findings.py), so the page and the AI always agree. Azure OpenAI settings come from .env
(see .env.example).
"""
import importlib.util
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai.assistant import ENV_VARS, Assistant  # noqa: E402
from ai.findings import prioritized_failures  # noqa: E402

log = logging.getLogger(__name__)

ASSISTANT_AVATAR = ":material/shield_lock:"
USER_AVATAR = ":material/person:"

STATUS_BADGE = {  # status -> (label, color, icon)
    "PASS": ("Pass", "green", ":material/check_circle:"),
    "FAIL": ("Fail", "red", ":material/cancel:"),
    "NOT ASSESSED": ("Not assessed", "gray", ":material/help:"),
}
PRIORITY_BADGE = {"high": ("High priority", "red"), "moderate": ("Moderate priority", "orange")}

SUGGESTIONS = {
    ":material/low_priority: What should we fix first?": "What should we fix first, and why?",
    ":material/gpp_maybe: Our biggest identity risk": "What is our biggest identity risk right now?",
    ":material/admin_panel_settings: Privileged access": "How are we doing on privileged access?",
    ":material/passkey: MFA and sign-in": "How are we doing on MFA and sign-in protection?",
}

GENERIC_ERROR = ("The AI service didn't return an answer. Check the Azure OpenAI settings in `.env` "
                 "and your network connection, then try again.")


# --- Data and AI -------------------------------------------------------------
@st.cache_resource(show_spinner=False, ttl="10m", max_entries=1)
def get_assistant():
    """The verified findings plus the AI client. Reloaded every 10 minutes to pick up new scans."""
    return Assistant()


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


# --- Report (verified facts only, no AI) --------------------------------------
def format_time(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    except (TypeError, ValueError):
        return iso or "unknown"


def build_report(findings, info, summary):
    failures = prioritized_failures(findings)
    lines = [
        "# SCuBA assessment report: Microsoft Entra ID",
        "",
        f"- Tenant: {info.get('tenant') or 'unknown'} ({info.get('domain') or 'unknown domain'})",
        f"- Scanned: {format_time(info.get('scan_time'))} with ScubaGear {info.get('tool_version') or 'unknown'}",
        f"- Generated: {datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M UTC')}",
        "",
        "_Built from the verified OSCAL results. This report contains no AI-generated content._",
        "",
        "## Summary",
        "",
        "| Passing | Failing | Not assessed | Total |",
        "|---|---|---|---|",
        f"| {summary['passed']} | {summary['failed']} | {summary['not_assessed']} | {summary['total']} |",
        "",
        "## Controls to fix, in priority order",
        "",
    ]
    if not failures:
        lines += ["No failing controls.", ""]
    for f in failures:
        lines += [
            f"### {f.control_id}: {f.title}",
            "",
            f"**Priority:** {f.priority} · **Obligation:** {f.obligation} · **Area:** {f.group}",
            "",
            f"**Requirement:** {f.requirement}",
            "",
            f"**Scan result:** {f.finding or 'No details recorded.'}",
            "",
            "**Remediation (SCuBA guidance)**",
            "",
            f.remediation or "None recorded.",
            "",
        ]
    passing = [f for f in findings if f.status == "PASS"]
    if passing:
        lines += ["## Passing controls", "", "| Control | Title |", "|---|---|"]
        lines += [f"| {f.control_id} | {f.title} |" for f in passing]
        lines.append("")
    unassessed = [f for f in findings if f.status == "NOT ASSESSED"]
    if unassessed:
        lines += ["## Not assessed", "", "| Control | Title |", "|---|---|"]
        lines += [f"| {f.control_id} | {f.title} |" for f in unassessed]
        lines.append("")
    return "\n".join(lines)


# --- Rendering ----------------------------------------------------------------
def status_badge(status, label=None, help=None):
    text, color, icon = STATUS_BADGE.get(status, (status, "gray", None))
    st.badge(label or text, color=color, icon=icon, help=help)


def render_fact(f):
    """One verified record, straight from OSCAL."""
    st.markdown(f"**{f['control_id']}** · {f['title']}")
    with st.container(horizontal=True, gap="xsmall"):
        status_badge(f["status"])
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
                status_badge(f["status"], label=f["control_id"], help=f"{f['title']}: {f['status'].lower()}")
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


def queue(kind, prompt, control_id=None):
    st.session_state.pending = {"kind": kind, "prompt": prompt, "control_id": control_id}


def pick_suggestion():
    choice = st.session_state.suggestion
    if choice:
        queue("chat", SUGGESTIONS[choice])
    st.session_state.suggestion = None


def clear_conversation():
    st.session_state.messages = []
    st.session_state.pending = None


# --- Page ---------------------------------------------------------------------
st.set_page_config(page_title="SCuBA posture assistant", page_icon=ASSISTANT_AVATAR, layout="centered")

try:
    assistant = get_assistant()
except Exception:
    log.exception("Could not load the OSCAL results")
    st.error("Couldn't load `oscal/findings.json`. Regenerate it with `comparison/compare_oscal.py`, "
             "then reload this page.", icon=":material/error:")
    st.stop()

findings, info, summary = assistant.findings, assistant.info, assistant.summary
failures = prioritized_failures(findings)
problem = ai_problem()

with st.sidebar:
    st.markdown(f"### {ASSISTANT_AVATAR} SCuBA assistant")
    st.caption("CISA SCuBA baseline for Microsoft Entra ID (MS.AAD)")
    if problem:
        st.badge("AI offline", icon=":material/cloud_off:", color="red")
        st.caption(problem.replace("`", ""))  # inline code is unreadable on the dark sidebar theme
    else:
        st.badge("AI ready", icon=":material/cloud_done:", color="green")

    st.space("small")
    st.markdown("**Scan**")
    st.markdown(
        f":material/domain: {info.get('tenant') or 'Unknown tenant'}  \n"
        f":material/language: {info.get('domain') or 'Unknown domain'}  \n"
        f":material/schedule: {format_time(info.get('scan_time'))}  \n"
        f":material/build: ScubaGear {info.get('tool_version') or 'unknown'}"
    )

    st.space("small")
    st.markdown("**Actions**")
    st.button("Executive summary", icon=":material/summarize:", width="stretch", disabled=bool(problem),
              on_click=queue, args=("summary", "Give me an executive summary of our Entra ID security posture."))
    st.download_button("Download report", data=lambda: build_report(findings, info, summary),
                       file_name="scuba-assessment-report.md", mime="text/markdown",
                       icon=":material/download:", width="stretch", on_click="ignore",
                       help="Verified scan results and SCuBA remediation steps. No AI content.")
    st.button("New conversation", icon=":material/restart_alt:", type="tertiary", on_click=clear_conversation,
              disabled=not st.session_state.messages)

st.title("SCuBA posture assistant", icon=ASSISTANT_AVATAR)
st.caption("Ask about your tenant's CISA SCuBA assessment. Answers are AI-generated and cite the "
           "verified OSCAL results they rely on.")

with st.container(horizontal=True):
    st.metric("Passing", f"{summary['passed']} of {summary['assessed']}", icon=":material/check_circle:",
              border=True, help="Assessed controls that meet the SCuBA requirement")
    st.metric("Failing", summary["failed"], icon=":material/cancel:", border=True)
    st.metric("High priority", sum(f.priority == "high" for f in failures), icon=":material/priority_high:",
              border=True, help="Failed SHALL and SHALL NOT requirements")
    st.metric("Not assessed", summary["not_assessed"], icon=":material/help:", border=True)

label = (f"{len(failures)} control{'s' if len(failures) != 1 else ''} need attention" if failures
         else "All assessed controls pass")
with st.expander(label, icon=":material/fact_check:"):
    for f in failures:
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(f"**{f.control_id}** · {f.title}", width="stretch")
                text, color = PRIORITY_BADGE[f.priority]
                st.badge(text, color=color)
                st.button("Ask AI", key=f"explain_{f.control_id}", icon=":material/auto_awesome:",
                          type="tertiary", disabled=bool(problem), on_click=queue,
                          args=("explain", f"Explain `{f.control_id}`: what it requires, what the scan "
                                           "found, and how to fix it.", f.control_id))
            st.caption(f.requirement)
    st.markdown("**All controls**")
    st.dataframe(
        [{"Control": f.control_id, "Title": f.title, "Obligation": f.obligation,
          "Status": STATUS_BADGE.get(f.status, (f.status,))[0]} for f in findings],
        hide_index=True,
        column_config={"Control": st.column_config.TextColumn(width="small"),
                       "Obligation": st.column_config.TextColumn(width="small"),
                       "Status": st.column_config.TextColumn(width="small")},
    )

typed = st.chat_input("AI is offline, see the sidebar" if problem else "Ask about your Entra ID security posture",
                      submit_mode="disable", disabled=bool(problem))
request = {"kind": "chat", "prompt": typed, "control_id": None} if typed else st.session_state.pending
st.session_state.pending = None

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
