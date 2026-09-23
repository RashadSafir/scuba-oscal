# SCuBA posture assistant

A Streamlit chat app for asking natural-language questions about a Microsoft 365
Entra ID (MS.AAD) security posture. Answers are grounded in OSCAL assessment data
and cite the SCuBA controls they rely on.

This repo is the **frontend only**. It runs on its own using demo data from
`stub.py`, and switches to the real AI backend automatically once that exists.

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501.

## Files

| File | Purpose |
|---|---|
| `app.py` | The Streamlit app: chat, posture matrix, sidebar, report download. |
| `stub.py` | Demo backend with canned, cited answers for the 11 in-scope controls. Also supplies the control list for the posture matrix. |
| `.streamlit/config.toml` | Theme (colors, fonts) and the setting that hides error tracebacks from the UI. |
| `requirements.txt` | Python dependencies (`streamlit` only). |
| `FRONTEND-SPEC.md` | Original requirements, including the backend contract. |

## Connecting the real backend

`app.py` tries to import from a module named `capability` and falls back to the
stub if it isn't found:

```python
try:
    from capability import answer, generate_report
except ImportError:
    from stub import answer, generate_report
```

To connect the backend, add `capability.py` (or a `capability` package) next to
`app.py` that provides these two functions:

```python
def answer(question: str) -> dict:
    """Return {"text": str, "citations": [{"id": "MS.AAD.3.1v1", "kind": "control"}, ...]}.

    "text" may contain markdown. An optional "evidence" key (a list of OSCAL
    snippet strings) fills the "Show OSCAL evidence" expander under each answer.
    """

def generate_report() -> str:
    """Return a markdown compliance report."""
```

No changes to `app.py` are needed. When the stub is in use, the sidebar shows
"Showing demo data from the stub backend."

**Note:** the posture matrix and the pass/fail dots on citation chips still read
from `CONTROLS` in `stub.py`. When real scan results are available, update that
list or point `app.py` at the backend's data.

## Other settings

- `SCHEMA_VALID` in `app.py` controls the "Validated against NIST OSCAL schema"
  badge. Set it from the real validation result once the pipeline provides one.
- API keys for the backend belong in `.streamlit/secrets.toml`, which is
  git-ignored. Never commit it.
