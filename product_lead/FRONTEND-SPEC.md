# Frontend Requirements — SCuBA → OSCAL demo app

## Context

Build a **Streamlit** web app for a hackathon project. The app is a chat interface where a
security professional asks natural-language questions about their Microsoft 365 security posture
and gets **grounded, cited answers** generated from OSCAL data. This is the "face" of a larger
pipeline (other teammates build the data/AI layers); **this task is only the frontend.**

The app must **run today against a stub**, before the real AI backend exists, and later connect to
that backend by changing a single import.

## Tech stack & constraints

- Python 3 + Streamlit. Start as a single `app.py` (a small `stub.py` helper is fine).
- Run with: `streamlit run app.py` — must work with **no arguments and no backend**.
- Use `st.session_state` for all state. **Do not use** localStorage/sessionStorage or any browser storage.
- Keep dependencies minimal: `streamlit` only for now (`anthropic` comes later, in the backend, not here).
- Do **not** build any AI logic, file parsing, or OSCAL handling in the frontend — see Non-goals.

## The integration contract (most important — do not change the shape)

The frontend talks to the AI backend through **one function**:

```python
def answer(question: str) -> dict:
    """Returns:
    {
      "text": str,                      # the answer, may contain markdown
      "citations": [                    # zero or more
         {"id": "MS.AAD.3.1v1", "kind": "control"},
         ...
      ]
    }
    """
```

Requirements:
- Import this from a module (e.g. `from capability import answer`), but **ship a stub fallback**
  so the app runs standalone if that import fails. A `try/except ImportError` that falls back to a
  local stub is ideal.
- The report generator is a second backend function; stub it the same way:
  ```python
  def generate_report() -> str:   # returns markdown text
  ```

## Domain context

- The app is about **Microsoft 365 SCuBA Entra ID (MS.AAD)** controls.
- Scope is **11 controls**. Answers reference controls by IDs like `MS.AAD.3.1v1`.
- Use these 11 (with their real pass/fail) so the stub and demo feel authentic:

  | Control | Meaning | Obligation | Result |
  |---|---|---|---|
  | MS.AAD.1.1v1 | Block legacy auth | Shall | PASS |
  | MS.AAD.2.1v1 | Block high-risk users | Shall | PASS |
  | MS.AAD.2.3v1 | Block high-risk sign-ins | Shall | PASS |
  | MS.AAD.3.1v1 | Phishing-resistant MFA, all users | Shall | FAIL |
  | MS.AAD.3.8v1 | Managed devices to register MFA | Should | FAIL |
  | MS.AAD.3.9v1 | Block device code auth | Should | FAIL |
  | MS.AAD.5.2v1 | Restrict user app consent | Shall | FAIL |
  | MS.AAD.6.1v1 | Passwords shall not expire | Shall | FAIL |
  | MS.AAD.7.1v1 | Global Admin 2–8 users | Shall | PASS |
  | MS.AAD.7.4v1 | No permanent privileged assignments | Shall | FAIL |
  | MS.AAD.8.1v1 | Limit guest access | Should | PASS |

  (Summary: 11 controls, 5 pass, 6 fail.)

## Functional requirements

1. **Chat interface** — a chat input box; message history that persists across reruns via
   `st.session_state`; user and assistant messages shown as chat bubbles (`st.chat_message`).
2. **Citations** — every assistant answer displays its cited control IDs as **visible chips/badges**
   directly under the answer text (e.g. small rounded tags showing `MS.AAD.3.1v1`).
3. **Sidebar** containing:
   - A **schema-valid badge**: "✓ Validated against NIST OSCAL schema", driven by a boolean flag
     (so it can later reflect a real validation result).
   - **Seeded example questions** as clickable buttons (exact list below). Clicking one submits it
     as if typed.
   - A **report download button** that downloads a `.md` compliance report (from `generate_report()`).
4. **Report generation** — the download button calls `generate_report()` and offers the returned
   markdown as a downloadable `scuba-compliance-report.md` via `st.download_button`.
5. **Graceful errors** — wrap the `answer()` call in `try/except`; on failure show a friendly inline
   message. **Never** let a raw traceback appear in the UI.
6. **Loading indicator** — show `st.spinner("Reading your OSCAL data…")` while an answer is generated.
7. **Scan summary (nice-to-have)** — a small metric row near the top: total controls / passing /
   failing (11 / 5 / 6).

## Seeded demo questions (use exactly these)

- "What's my biggest identity risk?"
- "What should I fix first, and why?"
- "How am I doing on MFA?"

## Stub behavior (so it runs before the backend is ready)

- Provide a stub `answer()` that returns realistic, **cited** canned responses for the seeded
  questions, using the real 11 controls and their pass/fail. In particular:
  - "biggest risk" → highlight `MS.AAD.3.1v1` (phishing-resistant MFA not enforced), a failed Shall.
  - "fix first" → a prioritized list with failed **Shalls** (3.1, 5.2, 6.1, 7.4) above failed
    **Shoulds** (3.8, 3.9), to demonstrate priority ordering.
  - "MFA" → summarize the MFA-related controls (3.x) and their status.
  - Any other question → a sensible default that says answers come from the OSCAL data.
- Provide a stub `generate_report()` returning a short markdown compliance report listing the
  failing controls.

## UI / UX requirements

- Clean, professional, readable — this is shown to judges and screen-recorded on a laptop.
- Centered layout; a clear title and one-line caption.
- Citations must be visually distinct (colored pill/badge), not plain text.
- Markdown in answers should render (lists, bold).

## Non-goals (do NOT build these here)

- No real LLM/API calls in the frontend — that lives behind `answer()` (a teammate owns it).
- No parsing of OSCAL or ScubaGear files in the frontend.
- No authentication, no database, no browser localStorage/sessionStorage.
- No heavy custom CSS or complex theming — keep it simple and reliable over fancy.

## Acceptance criteria

- Runs with `streamlit run app.py` with **no backend present** (stub path works).
- All three seeded questions return answers **with citation chips**.
- The "fix first" answer visibly orders failed Shalls above failed Shoulds.
- The report button downloads a `.md` file.
- Switching from stub to the real backend requires changing **one import line**.
- No raw tracebacks ever appear in the UI.

## Nice-to-haves (only if time remains)

- Scan-summary metric row (controls / pass / fail).
- A "Show evidence" expander under each answer that displays the raw OSCAL snippet(s) behind it.
- Light/dark theme friendliness.
