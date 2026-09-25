"""The Finding model: one record per catalog control, read from findings.json.

Input : oscal/findings.json   built by comparison/compare_oscal.py from the SCuBA catalogs in
                              oscal/Controls and the ScubaGear oscal/assessment-results.json

This is plain code, not AI. Everything in a Finding is copied from findings.json; nothing is
inferred. The AI layer (assistant.py) receives these records as its only facts.

Status (findings.json status -> Finding status):
  PASS           -> PASS
  FAIL           -> FAIL           ScubaGear Fail
  WARNING        -> FAIL           ScubaGear Warning (a failed SHOULD); scuba_result keeps "Warning"
  NOT_ASSESSED   -> NOT ASSESSED   the control is in the catalog but ScubaGear has no result for it
"""
import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FINDINGS = ROOT / "oscal" / "findings.json"

# Same obligation -> priority mapping as pipeline/make_poam.py. Keep the two in step.
PRIORITY = {"SHALL": "high", "SHALL NOT": "high", "SHOULD": "moderate"}
STATES = {"PASS": "PASS", "FAIL": "FAIL", "WARNING": "FAIL", "NOT_ASSESSED": "NOT ASSESSED"}
PRIORITY_ORDER = {"high": 0, "moderate": 1}


@dataclass(frozen=True)
class Finding:
    control_id: str        # policy id as SCuBA writes it, e.g. MS.AAD.7.4v1
    title: str
    group: str             # catalog group title, e.g. "Privileged Access"
    obligation: str        # SHALL | SHALL NOT | SHOULD
    status: str            # PASS | FAIL | NOT ASSESSED
    priority: str | None   # high | moderate for failures, None otherwise
    requirement: str       # catalog statement
    rationale: str         # catalog guidance: why the control matters
    finding: str           # ScubaGear's details for this tenant
    evidence: str          # where the verdict came from (report, product, group, raw result)
    scuba_result: str      # ScubaGear's own word: Pass | Fail | Warning | "" if not assessed
    remediation: str       # catalog remediation steps, HTML stripped
    nist: list[str]        # related NIST SP 800-53 controls from the catalog links
    # where the record came from in OSCAL ("" when the control was not assessed or findings.json predates them)
    oscal_control_id: str = ""   # catalog control id, e.g. ms.aad.7.4v1
    finding_uuid: str = ""       # assessment-results finding
    observation_uuid: str = ""   # its observation (the evidence)
    risk_uuid: str = ""          # its risk, failures only

    def to_dict(self):
        return asdict(self)


def strip_html(text):
    """Keep the words and markdown links; drop HTML tags such as <pre> and <b>."""
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_findings(findings_path=FINDINGS):
    """Every catalog control as a Finding, in catalog order."""
    out = []
    for a in _read(findings_path)["assessments"]:
        if a["status"] not in STATES:
            raise ValueError(f"{a['control_id']}: unexpected status {a['status']!r}")
        status, obligation = STATES[a["status"]], a.get("obligation") or ""
        oscal = a.get("oscal") or {}
        out.append(Finding(
            control_id=a["control_id"], title=a.get("title") or "", group=a.get("group") or "",
            obligation=obligation, status=status,
            priority=PRIORITY.get(obligation) if status == "FAIL" else None,
            requirement=a.get("requirement") or "", rationale=a.get("guidance") or "",
            finding=a.get("finding") or "", evidence=" ".join(a.get("evidence") or []),
            scuba_result=a.get("scubagear_result") or "",
            remediation=strip_html(a.get("remediation_guidance")),
            nist=list(a.get("nist") or []),
            oscal_control_id=a.get("oscal_control_id") or "",
            finding_uuid=oscal.get("finding_uuid") or "",
            observation_uuid=next(iter(oscal.get("observation_uuids") or []), ""),
            risk_uuid=next(iter(oscal.get("risk_uuids") or []), "")))
    return out


def load_assessment_info(findings_path=FINDINGS):
    """Scan-level facts safe to show the AI: tenant display name, scan time, tool version.

    findings.json carries no tenant id or report uuid; the AI does not need them."""
    scan = _read(findings_path).get("metadata", {}).get("scan", {})
    return dict(tenant=scan.get("tenant") or "", domain=scan.get("domain") or "",
                scan_time=scan.get("scan_time") or "", tool_version=scan.get("tool_version") or "")


def summarize(findings):
    """Counts the dashboard and the AI both use, so they can never disagree."""
    count = lambda s: sum(f.status == s for f in findings)  # noqa: E731
    return dict(assessed=count("PASS") + count("FAIL"), passed=count("PASS"), failed=count("FAIL"),
                not_assessed=count("NOT ASSESSED"), total=len(findings))


def prioritized_failures(findings):
    """Failed controls, high priority (SHALL / SHALL NOT) before moderate (SHOULD), catalog order within."""
    return sorted((f for f in findings if f.status == "FAIL"), key=lambda f: PRIORITY_ORDER[f.priority])
