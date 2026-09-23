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
- oscal/assessment-results.json
- oscal/poam.json

## AI interface (Role 3)
answer(question: str) -> {"text": str, "citations": list[str]}
