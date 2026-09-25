# scuba-oscal

We converted the CISA SCuBA Microsoft Entra ID baseline (MS.AAD) into an OSCAL Catalog and Profile, with each policy linked to its NIST SP 800-53 controls. When a user uploads a ScubaGear results file, the app converts it into OSCAL Assessment Results, links every observation and finding to its SCuBA control, and generates an OSCAL POA&M for the failed requirements. Every file is checked against the OSCAL 1.1.2 models, and the original scan is recorded with its SHA-256 hash so auditors can check the evidence was not altered.

The app shows pass, fail and not-assessed counts and a "Fix first" list, lets people inspect evidence and fix steps, answers questions with the AI (citing the controls and OSCAL findings behind each answer), and produces a PDF report. Statuses always come from the scan; anything the AI writes is labelled as analysis.

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
streamlit run app/app.py
```

Upload a ScubaGear `ScubaResults*.json` file, or select **Try the sample scan**. For the AI features, copy `.env.example` to `.env` and fill in the Azure OpenAI settings; check the connection with `python scripts/check_llm.py`. Without them, everything except questions and analyst notes still works, and the PDF report holds the verified results only.

## How it works

```
ScubaGear JSON ──► make_assessment_results.py ──► Assessment Plan + Assessment Results (back-matter: scan + SHA-256)
SCuBA catalog  ──► make_profile.py            ──► Profile (the controls in scope)
Assessment Results ──► make_poam.py           ──► POA&M (one item per failed control)
Catalog + Assessment Results ──► compare_oscal.py ──► findings.json ──► app, AI, PDF
All OSCAL files ──► validate_oscal.py (OSCAL 1.1.2 models, unique uuids)
```

| Folder | What is there |
|---|---|
| `oscal/Controls/` | The SCuBA MS.AAD catalog in OSCAL (34 policies) |
| `oscal/` | Generated OSCAL files and `findings.json` for the committed sample |
| `pipeline/` | Catalog, profile, assessment results, POA&M builders and the validator |
| `comparison/` | `compare_oscal.py`: joins the catalog with the results, control by control |
| `ai/` | The AI layer: facts from `findings.json`, prompts, Azure OpenAI client |
| `app/` | The Streamlit app and the PDF report |
| `data/sample/` | Two fictional ScubaGear scans |

Uuids are derived from the inputs (uuid5), so the same scan always gives the same ids. Validate any file with `python pipeline/validate_oscal.py FILE ...`.

## Scope

The baseline in scope is Microsoft Entra ID (MS.AAD). Another SCuBA baseline can be added by putting its OSCAL catalog in `oscal/Controls/`; the pipeline reads every catalog there.

## Data handling

An uploaded scan is processed in memory on the server and never saved; it is gone when the page is refreshed. When questions, analyst notes or the AI part of the report are used, the scan's per-control results are sent to the configured Azure OpenAI deployment. The AI cannot change a status or write to any file.

## Tests

```bash
python -m pytest -q tests/test_ai.py tests/test_compare_oscal.py tests/test_assessment_results.py tests/test_poam.py tests/test_profile_validate.py
```

`tests/test_catalog.py` and `tests/test_catalog_full.py` rebuild the committed catalogs; restore them afterwards with `git restore oscal/catalog.json oscal/Controls/EntraID-catalog-full.json`.
