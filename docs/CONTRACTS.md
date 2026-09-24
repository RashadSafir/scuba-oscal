# Team Contracts
## ID convention (Role 1 proposes, team ratifies)
- Control id: policy id lowercased, e.g. `MS.AAD.7.4v1` → `ms.aad.7.4v1`
- Statement part id: control id + `_smt`, e.g. `ms.aad.7.4v1_smt` (findings set `target-id` to this)
- Guidance part id: control id + `_gdn`, e.g. `ms.aad.7.4v1_gdn`  - Remediation part id: control id + `_rem`, e.g. `ms.aad.7.4v1_rem` (the "how to fix" text from aad.md; use it for POA&M remediation)
- Group id: `ms.aad.` + section number, e.g. `ms.aad.7`
- Obligation prop: `name="obligation"`, value `SHALL` | `SHALL NOT` | `SHOULD` (a failed SHALL or SHALL NOT outranks a failed SHOULD)
- Stability: ids come only from the policy id, so they never change between runs (checked by `python -m pytest -v tests`)
- Controls in scope: `ms.aad.1.1v1`, `ms.aad.2.1v1`, `ms.aad.2.3v1`, `ms.aad.3.8v1`, `ms.aad.5.2v1`, `ms.aad.7.1v1`, `ms.aad.7.4v1`, `ms.aad.8.1v1`


## Shared file paths
- oscal/catalog.json
- oscal/Controls/*.json (SCuBA catalogs, input to comparison/compare_oscal.py)
- oscal/findings.json (compare_oscal.py output; the AI layer's only input)
- oscal/assessment-results.json
- oscal/poam.json

## AI interface (Role 3)
Code: `ai/` (`from ai import answer, default_assistant`). Facts come from `ai/findings.py` (plain code, no AI), which reads `oscal/findings.json`.

```
answer(question: str, history: list[{"role", "content"}] | None = None) -> {
    "text": str,                     # AI-generated analysis (Markdown); label it as AI output
    "verified": list[Finding dict],  # OSCAL facts for every control the text cites
    "citations": list[{"id", "kind", "status", "title"}],
    "unverified_references": list[str],  # ids the model mentioned that are not in the assessment
}
default_assistant().explain(control_id)      # same shape, for "Ask AI about this finding"
default_assistant().executive_summary()      # same shape
default_assistant().compliance_report()      # same shape; long-form Markdown report (the app turns it into a PDF)
ai.reload()                                  # after oscal/findings.json is regenerated (new upload)
```
Finding fields: control_id, title, group, obligation, status (PASS | FAIL | NOT ASSESSED), priority,
requirement, rationale, finding, evidence, scuba_result, remediation, nist
