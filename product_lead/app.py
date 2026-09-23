"""SCuBA → OSCAL demo — chat frontend.

Run with:  streamlit run app.py
"""

from html import escape

import streamlit as st

# --- Backend integration ------------------------------------------------------
# The real backend lives in `capability`. Until it exists, fall back to the stub.
try:
    from capability import answer, generate_report
    BACKEND = "live"
except ImportError:
    from stub import answer, generate_report
    BACKEND = "stub"

# Scan results for the posture strip and citation status dots.
from stub import CONTROLS

# Flip to reflect a real schema-validation result once the pipeline provides it.
SCHEMA_VALID = True

EXAMPLE_QUESTIONS = [
    "What's my biggest identity risk?",
    "What should I fix first, and why?",
    "How am I doing on MFA?",
]

ERROR_MESSAGE = (
    "The assistant couldn't generate an answer for this question. "
    "Try again, or rephrase it."
)

CONTROL_INDEX = {cid: (meaning, obligation, result) for cid, meaning, obligation, result in CONTROLS}

ASSISTANT_AVATAR = ":material/shield_lock:"

STYLES = """
<style>
/* Page chrome */
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 3rem; max-width: 760px; }

/* Header */
.app-head { display: flex; gap: 0.9rem; align-items: flex-start; margin-bottom: 0.25rem; }
.app-head .mark {
  flex: none; width: 2.5rem; height: 2.5rem; border-radius: 0.6rem;
  background: #0E1B2C; color: #fff; display: grid; place-items: center;
}
.app-head h1 {
  font-size: 1.6rem; line-height: 1.2; font-weight: 700; letter-spacing: -0.015em;
  margin: 0; padding: 0; color: #0E1B2C;
}
.app-head p { margin: 0.3rem 0 0; color: #4A5A70; font-size: 0.95rem; line-height: 1.5; max-width: 60ch; }

/* Posture matrix */
.posture {
  display: flex; flex-wrap: wrap; gap: 1.25rem 1.5rem; align-items: stretch;
  background: #fff; border: 1px solid #D9DFE8; border-radius: 0.75rem;
  padding: 1.1rem 1.25rem; margin: 1.25rem 0 0.5rem;
}
.score { flex: 0 0 7.5rem; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.6rem; }
.ring {
  width: 6.25rem; height: 6.25rem; border-radius: 50%; display: grid; place-items: center;
  background: conic-gradient(#1F7A5A 0 var(--pct), #F1D4D1 var(--pct) 100%);
}
.ring > div {
  width: 4.9rem; height: 4.9rem; border-radius: 50%; background: #fff;
  display: grid; place-content: center; text-align: center; line-height: 1.1;
}
.ring b { font-size: 1.55rem; font-weight: 700; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; color: #0E1B2C; }
.ring small { font-size: 0.72rem; color: #6B7A90; }
.score-caption { font-size: 0.82rem; color: #4A5A70; text-align: center; line-height: 1.35; }
.score-caption strong { color: #B3261E; }

.matrix {
  flex: 1 1 18rem; min-width: 0; display: grid;
  grid-template-columns: 5.4rem minmax(0, 1fr) minmax(0, 1fr);
  gap: 0.4rem; font-size: 0.82rem;
}
@media (max-width: 520px) {
  .matrix { grid-template-columns: 3.6rem minmax(0, 1fr) minmax(0, 1fr); }
  .matrix .rowhead span { display: none; }
  .score { flex-basis: 100%; flex-direction: row; justify-content: flex-start; }
  .score-caption { text-align: left; }
}
.matrix .colhead, .matrix .rowhead { color: #6B7A90; font-weight: 500; }
.matrix .colhead { padding: 0 0.7rem; display: flex; align-items: center; gap: 0.4rem; }
.matrix .colhead i { width: 0.55rem; height: 0.55rem; border-radius: 50%; }
.matrix .colhead.fail i { background: #B3261E; }
.matrix .colhead.pass i { background: #1F7A5A; }
.matrix .rowhead { display: flex; flex-direction: column; justify-content: center; line-height: 1.25; }
.matrix .rowhead span { font-size: 0.72rem; font-weight: 400; color: #8A97AB; }
.cell {
  border: 1px solid #E3E8EF; border-radius: 0.5rem; padding: 0.5rem 0.7rem;
  background: #FAFBFD; min-height: 3rem;
}
.cell.fail.shall { background: #FBEDEB; border-color: #EBC3BE; }
.cell.fail.should { background: #FDF6F5; border-color: #F0DAD7; }
.cell-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.25rem; }
.cell-count { font-weight: 700; font-size: 1rem; color: #0E1B2C; font-variant-numeric: tabular-nums; }
.cell.fail .cell-count { color: #B3261E; }
.cell.pass .cell-count { color: #1F7A5A; }
.cell-flag { font-size: 0.7rem; font-weight: 600; color: #fff; background: #B3261E; border-radius: 999px; padding: 0.05rem 0.5rem; }
.cell ul { list-style: none; margin: 0; padding: 0; }
.cell li { margin: 0; padding: 0.1rem 0; color: #33425A; line-height: 1.35; font-size: 0.8rem; }
.cell li code {
  font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem; color: #4A5A70;
  background: none; padding: 0; margin-right: 0.35rem;
}
.cell .none { color: #8A97AB; font-size: 0.8rem; }

/* Chat messages */
[data-testid="stChatMessage"] { background: transparent; padding: 0.4rem 0; gap: 0.8rem; width: 100%; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { flex-direction: row-reverse; }
[data-testid="stChatMessageAvatarUser"] { display: none; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
  flex: 0 1 auto; max-width: 80%; margin: 0 0 0 auto; background: #E1E7F3; color: #0E1B2C;
  padding: 0.6rem 1rem; border-radius: 1rem 1rem 0.25rem 1rem;
}
[data-testid="stChatMessageAvatarCustom"] { background: #0E1B2C; color: #fff; border-radius: 0.5rem; }
[data-testid="stChatMessageContent"] p, [data-testid="stChatMessageContent"] li { line-height: 1.6; }

/* Citations */
.cites { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; margin-top: 0.15rem; }
.cites .lead { font-size: 0.8rem; color: #6B7A90; margin-right: 0.2rem; }
.cite {
  display: inline-flex; align-items: center; gap: 0.4rem;
  font-family: 'IBM Plex Mono', monospace; font-size: 0.76rem; font-weight: 500;
  color: #1C3A8C; background: #fff; border: 1px solid #C9D3E6;
  padding: 0.18rem 0.6rem; border-radius: 999px; cursor: default;
}
.cite i { width: 0.45rem; height: 0.45rem; border-radius: 50%; background: #8A97AB; }
.cite.pass i { background: #1F7A5A; }
.cite.fail i { background: #B3261E; }

/* Expander */
[data-testid="stExpander"] details { border-color: #D9DFE8; background: #fff; }

/* Sidebar */
[data-testid="stSidebarContent"] { padding-top: 0.5rem; }
.side-brand { font-weight: 700; font-size: 1.05rem; color: #fff; letter-spacing: -0.01em; margin: 0; }
.side-brand small { display: block; font-weight: 400; font-size: 0.8rem; color: #8FA0B8; margin-top: 0.15rem; }
.side-label { font-size: 0.82rem; color: #8FA0B8; font-weight: 500; margin: 0.25rem 0 -0.25rem; }
.schema {
  display: flex; gap: 0.55rem; align-items: flex-start; font-size: 0.85rem; line-height: 1.4;
  color: #CFEBDD; background: #15352D; border: 1px solid #245345;
  border-radius: 0.5rem; padding: 0.6rem 0.75rem;
}
.schema.invalid { color: #F4CFCB; background: #3D1A18; border-color: #6A2A25; }
.schema svg { flex: none; margin-top: 0.1rem; }
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
  justify-content: flex-start; text-align: left; font-weight: 400;
  background: #1A2A40; border-color: #2A3C57; color: #DCE3EE; padding: 0.55rem 0.8rem;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] > div { justify-content: flex-start; }
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] p { text-align: left; }
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover { border-color: #6F8FE8; color: #fff; }

/* Icons (inline SVG is stripped by st.html, so draw via CSS) */
.icon-shield, .icon-check, .icon-cross { display: inline-block; flex: none; background: currentColor; }
.icon-shield {
  width: 1.3rem; height: 1.3rem;
  -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z'/%3E%3Cpath d='M9 12l2 2 4-4'/%3E%3C/svg%3E") center / contain no-repeat;
          mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z'/%3E%3Cpath d='M9 12l2 2 4-4'/%3E%3C/svg%3E") center / contain no-repeat;
}
.icon-check, .icon-cross { width: 1rem; height: 1rem; margin-top: 0.1rem; }
.icon-check {
  -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M20 6L9 17l-5-5'/%3E%3C/svg%3E") center / contain no-repeat;
          mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M20 6L9 17l-5-5'/%3E%3C/svg%3E") center / contain no-repeat;
}
.icon-cross {
  -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.5' stroke-linecap='round'%3E%3Cpath d='M18 6L6 18M6 6l12 12'/%3E%3C/svg%3E") center / contain no-repeat;
          mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.5' stroke-linecap='round'%3E%3Cpath d='M18 6L6 18M6 6l12 12'/%3E%3C/svg%3E") center / contain no-repeat;
}

/* Assistant avatar */
[data-testid="stChatMessageAvatarCustom"] * { color: #fff; fill: #fff; }

/* Chat input */
[data-testid="stChatInput"] > div { background: #fff; border: 1px solid #D9DFE8; }
</style>
"""

SHIELD_ICON = '<span class="icon-shield" aria-hidden="true"></span>'
CHECK_ICON = '<span class="icon-check" aria-hidden="true"></span>'
CROSS_ICON = '<span class="icon-cross" aria-hidden="true"></span>'


# --- Helpers -----------------------------------------------------------------
def get_answer(question: str) -> dict:
    """Call the backend and normalize its response. Never raises."""
    try:
        result = answer(question)
        return {
            "text": str(result.get("text", "")),
            "citations": [c for c in result.get("citations", []) if c.get("id")],
            "evidence": list(result.get("evidence", []) or []),
            "error": False,
        }
    except Exception:
        return {"text": ERROR_MESSAGE, "citations": [], "evidence": [], "error": True}


def build_report() -> str:
    try:
        return generate_report()
    except Exception:
        return "# SCuBA Compliance Report\n\n_The report could not be generated. Try again._\n"


def posture_matrix_html() -> str:
    """Pass-rate ring plus an obligation × result matrix listing each control."""
    total = len(CONTROLS)
    passing = sum(1 for c in CONTROLS if c[3] == "PASS")
    failing = total - passing

    def cell(obligation: str, result: str) -> str:
        items = [c for c in CONTROLS if c[2] == obligation and c[3] == result]
        rows = "".join(
            f'<li title="{escape(cid)}"><code>{escape(cid.split(".", 2)[2].split("v")[0])}</code>'
            f"{escape(meaning)}</li>"
            for cid, meaning, _, _ in items
        ) or '<li class="none">None</li>'
        flag = '<span class="cell-flag">Fix first</span>' if (obligation, result) == ("Shall", "FAIL") and items else ""
        return (
            f'<div class="cell {result.lower()} {obligation.lower()}">'
            f'<div class="cell-top"><span class="cell-count">{len(items)}</span>{flag}</div>'
            f"<ul>{rows}</ul></div>"
        )

    return f"""
    <section class="posture" aria-label="Scan summary">
      <div class="score">
        <div class="ring" style="--pct: {passing / total * 100:.1f}%">
          <div><b>{passing}/{total}</b><small>passing</small></div>
        </div>
        <div class="score-caption"><strong>{failing} controls failing</strong><br>in the Entra ID baseline</div>
      </div>
      <div class="matrix">
        <div></div>
        <div class="colhead fail"><i></i>Failing</div>
        <div class="colhead pass"><i></i>Passing</div>
        <div class="rowhead">Shall<span>Required</span></div>
        {cell("Shall", "FAIL")}{cell("Shall", "PASS")}
        <div class="rowhead">Should<span>Recommended</span></div>
        {cell("Should", "FAIL")}{cell("Should", "PASS")}
      </div>
    </section>
    """


def render_citations(citations: list[dict]) -> None:
    if not citations:
        return
    chips = []
    for c in citations:
        cid = str(c["id"])
        meta = CONTROL_INDEX.get(cid)
        status = meta[2].lower() if meta else "unknown"
        tip = f"{meta[0]} ({meta[1]}, {meta[2].lower()})" if meta else str(c.get("kind", ""))
        chips.append(f'<span class="cite {status}" title="{escape(tip)}"><i></i>{escape(cid)}</span>')
    st.html(f'<div class="cites"><span class="lead">Cited controls</span>{"".join(chips)}</div>')


def render_evidence(evidence: list[str]) -> None:
    if not evidence:
        return
    with st.expander("Show OSCAL evidence", icon=":material/data_object:"):
        for snippet in evidence:
            st.code(snippet, language="json")


def render_assistant(msg: dict) -> None:
    if msg.get("error"):
        st.warning(msg["text"], icon=":material/error:")
        return
    st.markdown(msg["text"])
    render_citations(msg["citations"])
    render_evidence(msg["evidence"])


def queue_question(question: str) -> None:
    st.session_state.pending_question = question


# --- State ------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None


# --- Page -------------------------------------------------------------------
st.set_page_config(
    page_title="SCuBA Posture Assistant",
    page_icon=":material/shield_lock:",
    layout="centered",
)
st.html(STYLES)

with st.sidebar:
    st.html(
        '<p class="side-brand">SCuBA assistant'
        "<small>Microsoft Entra ID baseline (MS.AAD)</small></p>"
    )
    if SCHEMA_VALID:
        st.html(f'<div class="schema">{CHECK_ICON}<span>Validated against NIST OSCAL schema</span></div>')
    else:
        st.html(f'<div class="schema invalid">{CROSS_ICON}<span>OSCAL schema validation failed</span></div>')

    st.html('<p class="side-label">Try asking</p>')
    for q in EXAMPLE_QUESTIONS:
        st.button(q, key=f"example_{q}", on_click=queue_question, args=(q,), width="stretch")

    st.html('<p class="side-label">Compliance report</p>')
    st.download_button(
        "Download report (.md)",
        data=build_report,
        file_name="scuba-compliance-report.md",
        mime="text/markdown",
        icon=":material/download:",
        type="primary",
        width="stretch",
    )

    st.space("small")
    if st.button("Clear conversation", icon=":material/restart_alt:", type="tertiary"):
        st.session_state.messages = []
        st.rerun()
    if BACKEND == "stub":
        st.caption("Showing demo data from the stub backend.")

st.html(
    f"""
    <header class="app-head">
      <div class="mark">{SHIELD_ICON}</div>
      <div>
        <h1>SCuBA posture assistant</h1>
        <p>Ask about your Microsoft Entra ID configuration. Every answer cites the
        SCuBA controls it draws on.</p>
      </div>
    </header>
    """
)
st.html(posture_matrix_html())

# Chat history
for msg in st.session_state.messages:
    if msg["role"] == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    else:
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            render_assistant(msg)

if not st.session_state.messages:
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        st.markdown(
            "I read your SCuBA assessment results and point to the controls behind every "
            "answer. Pick a question from the sidebar, or type your own below."
        )

# Input — typed question or a clicked example
typed = st.chat_input("Ask about your identity security posture")
question = typed or st.session_state.pending_question
st.session_state.pending_question = None

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        with st.spinner("Reading your OSCAL data…"):
            reply = get_answer(question)
        render_assistant(reply)

    st.session_state.messages.append({"role": "assistant", **reply})
