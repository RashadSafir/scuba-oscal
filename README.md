# scuba-oscal

We converted the CISA SCuBA baselines for Microsoft 365 (Entra ID, Defender, Exchange Online, Power BI, Power Platform, SharePoint, Teams and the Security Suite) into OSCAL catalogs, with a Profile per assessment, with each policy linked to its NIST SP 800-53 controls. When a user uploads a ScubaGear results file, the app converts it into OSCAL Assessment Results, links every observation and finding to its SCuBA control, and generates an OSCAL POA&M for the failed requirements. Every file is checked against the OSCAL 1.1.2 models, and the original scan is recorded with its SHA-256 hash so auditors can check the evidence was not altered.

The app shows pass, fail and not-assessed counts, overall and by product, and a "Fix first" list with the reasoning behind its order. It lets people filter and inspect evidence and fix steps, compares a scan with an earlier one (resolved, regressed and new failures, with proposed POA&M closures a person confirms), answers questions with the AI (citing the controls and OSCAL findings behind each answer), and produces executive, auditor, engineer or complete PDF reports. Statuses always come from the scan; anything the AI writes is labelled as analysis.

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
Assessment Results ──► make_poam.py           ──► POA&M (one item per failed control, three undated milestones each)
Catalog + Assessment Results ──► compare_oscal.py ──► findings.json ──► app, AI, PDF
All OSCAL files ──► validate_oscal.py (OSCAL 1.1.2 models, unique uuids)
```

| Folder | What is there |
|---|---|
| `oscal/Controls/` | The SCuBA catalogs in OSCAL, one per product (128 policies) |
| `oscal/` | Generated OSCAL files and `findings.json` for the committed sample |
| `pipeline/` | Catalog, profile, assessment results, POA&M builders and the validator |
| `comparison/` | `compare_oscal.py`: joins the catalog with the results, control by control |
| `ai/` | The AI layer: facts from `findings.json`, prompts, Azure OpenAI client |
| `app/` | The Streamlit app and the PDF report |
| `data/sample/` | Fictional ScubaGear scans: `scuba_results_sample.json`, `scuba_results_sample_2.json` (contosodemo), its rescan nine days later `scuba_results_sample_2_rescan.json`, and the first contosodemo scan's OSCAL assessment results for the Changes tab |
| `config/fix_first.toml` | The Fix first weights (draft judgement calls for the team to review) |

Each POA&M item has three standard milestones (plan the change, apply it, verify with a ScubaGear rescan) and no dates: in the app's Findings tab, under Plan of action, the team sets a target date per item, which is added to the downloaded POA&M as the item's deadline.

Uuids are derived from the inputs (uuid5), so the same scan always gives the same ids. Validate any file with `python pipeline/validate_oscal.py FILE ...`.

## Scope

An upload is assessed against the catalogs for the products the scan covers: a catalog is used when the scan has results for its policy prefix (for example MS.TEAMS). Products the scan did not include are left out and named on the page, rather than counted as not assessed. A new baseline can be added by putting its OSCAL catalog in `oscal/Controls/`.

The committed files in `oscal/` (assessment results, POA&M, profile, findings) are an Entra ID example built from the sample scan.

## Data handling

An uploaded scan is processed in memory on the server and never saved; it is gone when the page is refreshed. The app's "How your data is handled" panel says what is sent to the AI; set `AZURE_OPENAI_HOSTING` and `AZURE_OPENAI_DATA_RETENTION` in `.env` to state where the AI runs and its retention. When questions, analyst notes or the AI part of the report are used, the scan's per-control results are sent to the configured Azure OpenAI deployment. The AI cannot change a status or write to any file.

## Tests

```bash
python -m pytest -q tests/test_ai.py tests/test_compare_oscal.py tests/test_assessment_results.py tests/test_poam.py tests/test_profile_validate.py tests/test_fix_first_and_changes.py
```

`tests/test_catalog.py` and `tests/test_catalog_full.py` rebuild the committed catalogs; restore them afterwards with `git restore oscal/catalog.json oscal/Controls/EntraID-catalog-full.json`.
